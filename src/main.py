"""
Main bot orchestration.
Coordinates scanning, analysis, trading, and Telegram approval workflow.
Runs as a continuous loop with periodic scanning.
"""

import logging
import asyncio
import os
import signal
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv
from pathlib import Path

# Add project root to path (needed when running as python src/main.py)
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from scanner import TokenScanner
from rug_checker import RugChecker
from chart_analyzer import ChartAnalyzer
from trade_executor import TradeExecutor
from telegram_bot import TradeBot
from logger import logger

load_dotenv()


class GemTraderBot:
    """
    Main bot class. Orchestrates all trading workflows.
    """
    
    def __init__(self):
        # Load config from env
        self.rpc_endpoint = os.getenv('RPC_ENDPOINT')
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        # Trading params
        self.min_liquidity = float(os.getenv('MIN_LIQUIDITY_USD', 5000))
        self.min_market_cap = float(os.getenv('MIN_MARKET_CAP_USD', 50000))
        self.min_age_minutes = int(os.getenv('MIN_AGE_MINUTES', 5))
        self.max_age = int(os.getenv('MAX_AGE_MINUTES', 60))
        self.max_holder_pct = float(os.getenv('MAX_HOLDER_CONCENTRATION', 0.50))
        self.min_buy_sell_ratio = float(os.getenv('MIN_BUY_SELL_RATIO', 0.5))
        self.min_volume_5m = float(os.getenv('MIN_VOLUME_5M_USD', 1000))
        self.volume_spike_min_ratio = float(os.getenv('VOLUME_SPIKE_MIN_RATIO', 1.5))
        self.profit_target = float(os.getenv('PROFIT_TARGET_PERCENT', 100))
        self.stop_loss = float(os.getenv('STOP_LOSS_PERCENT', 10))
        self.risk_per_trade = float(os.getenv('RISK_PERCENT', 5))
        self.wallet_balance_sol = float(os.getenv('WALLET_BALANCE_SOL', 1.0))
        
        # Initialize modules
        self.scanner = TokenScanner()
        self.rug_checker = RugChecker(self.rpc_endpoint)
        self.chart_analyzer = ChartAnalyzer()
        self.executor = TradeExecutor(self.rpc_endpoint)
        self.bot = TradeBot(
            token=self.telegram_token,
            chat_id=self.telegram_chat_id,
            executor=self.executor,
            scanner=self.scanner,
            rug_checker=self.rug_checker,
            chart_analyzer=self.chart_analyzer,
        ) if self.telegram_token else None
        
        # State
        self.running = True
        self.scanned_mints = set()  # Avoid re-scanning same tokens
        self.active_positions = {}
        self._reset_filters()
        
        # Wire callbacks
        if self.bot:
            self.bot.on_approve_callback = self._on_trade_approved
            self.bot.on_skip_callback = self._on_trade_skipped
            self.bot.on_stop_callback = self._on_stop_requested
    
    async def _on_trade_approved(self, trade_plan: dict) -> bool:
        """Handle user approving a trade."""
        try:
            logger.info(f"Trade approved: {trade_plan.get('token')}")
            
            # Track position
            mint = trade_plan.get('token_mint')
            if mint:
                self.active_positions[mint] = {
                    'symbol': trade_plan.get('token'),
                    'entry_price': trade_plan.get('entry_price'),
                    'amount_sol': trade_plan.get('buy_amount_sol'),
                    'entry_time': datetime.now().isoformat(),
                }
            
            # TODO: Generate and sign Jupiter swap transaction
            # This requires the user's wallet keypair
            
            return True
            
        except Exception as e:
            logger.error(f"Error executing trade: {e}")
            return False
    
    async def _on_trade_skipped(self, trade_id: str):
        """Handle user skipping a trade."""
        logger.info(f"Trade {trade_id} skipped by user")
    
    async def _on_stop_requested(self):
        """Handle stop request from Telegram."""
        logger.info("Stop requested by user")
        self.running = False
    
    async def _monitor_positions(self):
        """Check active positions for TP/SL."""
        if not self.active_positions:
            return
        
        for mint, pos in list(self.active_positions.items()):
            try:
                # Get current price
                token_info = self.scanner.get_token_info(mint)
                if not token_info:
                    continue
                
                current_price = token_info.get('price_usd', 0)
                
                # Calculate P&L
                pnl = self.executor.calculate_pnl(mint, current_price)
                if not pnl:
                    continue
                
                # Check exit conditions
                action = self.executor.check_exit_condition(
                    mint, current_price,
                    take_profit_pct=self.profit_target,
                    stop_loss_pct=self.stop_loss
                )
                
                # Send update if triggered
                if action and self.bot:
                    pnl['action'] = action
                    await self.bot.send_position_update(mint, pnl)
                    
                    if action in ['TAKE_PROFIT', 'STOP_LOSS']:
                        logger.info(f"Exit triggered for {pos['symbol']}: {action}")
                        # Position remains until user sells
                
            except Exception as e:
                logger.debug(f"Error monitoring {mint}: {e}")

    def _reset_filters(self):
        self._filter_counts = {
            'total': 0, 'market_cap': 0, 'age': 0, 'scam': 0,
            'holder': 0, 'buy_sell': 0, 'volume': 0, 'chart': 0,
            'execution': 0, 'alerts': 0,
        }

    async def run_scan_cycle(self):
        """Run one full scan -> analyze -> alert cycle."""
        logger.info("Starting scan cycle...")
        
        # Stage 1: Scan for trending tokens
        trending = self.scanner.scan_trending_tokens(
            top_n=10,
            min_liquidity_usd=self.min_liquidity
        )
        
        if not trending:
            logger.info("No trending tokens found")
            return

        self._reset_filters()

        # Filter out already scanned
        new_tokens = [t for t in trending if t['mint'] not in self.scanned_mints]
        self._filter_counts['total'] = len(new_tokens)

        for token in new_tokens:
            if not self.running:
                break
            
            mint = token['mint']
            self.scanned_mints.add(mint)
            
            logger.debug(f"Analyzing {token['symbol']} ({mint[:8]}...)")
            
            # Filter: market cap
            market_cap = token.get('market_cap_usd', 0)
            if self.min_market_cap > 0 and market_cap > 0 and market_cap < self.min_market_cap:
                logger.info(f"  Market cap filter: SKIPPED (${market_cap} < ${self.min_market_cap})")
                self._filter_counts['market_cap'] += 1
                continue
            
            # Filter: token age
            created_at = token.get('pair_created_at')
            if created_at and self.min_age_minutes > 0:
                try:
                    created_ts = created_at / 1000 if created_at > 1e10 else created_at
                    age_minutes = (datetime.now().timestamp() - created_ts) / 60
                    if age_minutes < self.min_age_minutes:
                        logger.info(f"  Age filter: SKIPPED ({age_minutes:.0f} min, need {self.min_age_minutes})")
                        self._filter_counts['age'] += 1
                        continue
                except Exception:
                    pass
            
            # Stage 2: Rug check
            rug_result = self.rug_checker.check_token(mint)
            
            if rug_result['is_likely_scam']:
                logger.info(f"  Scam filter: SKIPPED ({rug_result['risk_score']}/100)")
                self._filter_counts['scam'] += 1
                continue
            
            # Filter: holder concentration
            holder_pct = rug_result['checks'].get('holder_concentration', 0)
            if holder_pct > self.max_holder_pct:
                logger.info(f"  Holder filter: SKIPPED ({holder_pct:.0%} > {self.max_holder_pct:.0%})")
                self._filter_counts['holder'] += 1
                continue

            # Filter: buy/sell ratio (DexScreener txns data)
            txns_5m = token.get('txns_5m', {})
            buys_5m = int(txns_5m.get('buys', 0))
            sells_5m = int(txns_5m.get('sells', 0))
            if self.min_buy_sell_ratio > 0 and sells_5m > 0 and buys_5m > 0:
                ratio = buys_5m / sells_5m
                if ratio < self.min_buy_sell_ratio:
                    logger.info(f"  Buy/sell filter: SKIPPED (ratio {ratio:.2f} < {self.min_buy_sell_ratio})")
                    self._filter_counts['buy_sell'] += 1
                    continue

            # Filter: volume spike (optional)
            vol_5m = float(token.get('volume_5m_usd', 0))
            if self.min_volume_5m > 0 and vol_5m > 0 and vol_5m < self.min_volume_5m:
                logger.info(f"  Volume filter: SKIPPED (5m vol ${vol_5m:.0f} < ${self.min_volume_5m})")
                self._filter_counts['volume'] += 1
                continue
            
            # Stage 3: Chart analysis (with volume data)
            chart_result = self.chart_analyzer.analyze_token_chart(mint, token_data=token)
            
            if chart_result['signal'] not in ['BUY', 'STRONG_BUY']:
                logger.info(f"  Chart filter: SKIPPED ({chart_result['signal']})")
                self._filter_counts['chart'] += 1
                continue
            
            # Stage 4: Generate execution plan
            plan = self.executor.get_execution_plan(
                token_info=token,
                rug_result=rug_result,
                chart_result=chart_result,
                wallet_balance_sol=self.wallet_balance_sol,
                risk_pct=self.risk_per_trade
            )
            
            if not plan or plan['decision'] != 'EXECUTE':
                logger.info(f"  Execution filter: SKIPPED ({plan.get('reason') if plan else 'No plan'})")
                self._filter_counts['execution'] += 1
                continue
            
            # Stage 5: Send Telegram alert
            if self.bot:
                trade_id = await self.bot.send_alert(plan)
                if trade_id:
                    self._filter_counts['alerts'] += 1
                    logger.info(f"  Alert sent! Trade ID: {trade_id}")
                    await asyncio.sleep(5)
            else:
                logger.info(f"  Trade ready (no Telegram configured): {token['symbol']}")
                logger.info(f"  {plan}")

        # Cycle summary
        c = self._filter_counts
        filtered = c['total'] - c['alerts']
        logger.info(
            f"  Cycle: {c['total']} scanned, {filtered} filtered "
            f"(cap:{c['market_cap']} age:{c['age']} scam:{c['scam']} "
            f"hold:{c['holder']} bs:{c['buy_sell']} vol:{c['volume']} "
            f"chart:{c['chart']} exec:{c['execution']}) "
            f"→ {c['alerts']} alert(s)"
        )

    async def run(self):
        """Main bot loop."""
        logger.info("=" * 60)
        logger.info("GEM TRADER BOT STARTING")
        logger.info("=" * 60)
        
        # Start healthcheck HTTP server (required for Railway)
        health_task = asyncio.create_task(self._run_health_server())
        
        # Start Telegram bot polling if configured
        polling_task = None
        if self.bot:
            logger.info("Starting Telegram bot...")
            self.bot.register_handlers()
            await self.bot.app.initialize()
            await self.bot.app.start()
            polling_task = asyncio.create_task(self._run_telegram_polling())
            
            # Send startup notification
            await self.bot.send_notification(
                "Gem Trader Bot started!\n"
                "Scanning for opportunities every 60s."
            )
        
        # Main scanning loop
        scan_interval = 60
        monitor_interval = 30
        last_monitor = datetime.now()
        
        try:
            while self.running:
                cycle_start = datetime.now()
                
                # Run scan cycle
                await self.run_scan_cycle()
                
                # Run position monitoring every 30s
                if (datetime.now() - last_monitor).total_seconds() >= monitor_interval:
                    await self._monitor_positions()
                    last_monitor = datetime.now()
                
                # Sleep for remaining time
                elapsed = (datetime.now() - cycle_start).total_seconds()
                sleep_time = max(10, scan_interval - elapsed)
                logger.debug(f"Cycle complete. Next scan in {sleep_time:.0f}s")
                await asyncio.sleep(sleep_time)
                
        except asyncio.CancelledError:
            logger.info("Bot cancelled")
        except Exception as e:
            logger.error(f"Fatal error: {e}")
        finally:
            await self._shutdown(polling_task, health_task)
    
    async def _run_health_server(self):
        """Minimal HTTP healthcheck server for Railway."""
        port = int(os.getenv('PORT', 8080))
        logger.info(f"Starting healthcheck server on port {port}")
        
        async def handle(reader, writer):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
            await writer.drain()
            writer.close()
        
        try:
            server = await asyncio.start_server(handle, '0.0.0.0', port)
            async with server:
                await server.serve_forever()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Health server error (non-fatal): {e}")
    
    async def _run_telegram_polling(self):
        """Run Telegram polling in background."""
        try:
            await self.bot.app.updater.start_polling()
        except Exception as e:
            logger.error(f"Telegram polling error: {e}")
    
    async def _shutdown(self, polling_task=None, health_task=None):
        """Graceful shutdown."""
        logger.info("Shutting down...")
        self.running = False
        
        if polling_task:
            polling_task.cancel()
        if health_task:
            health_task.cancel()
        
        if self.bot:
            try:
                await self.bot.app.updater.stop()
                await self.bot.app.stop()
                await self.bot.app.shutdown()
            except:
                pass
        
        logger.info("Bot stopped")


def main():
    """Entry point."""
    bot = GemTraderBot()
    
    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        logger.info("Interrupt received, shutting down...")
        bot.running = False
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
