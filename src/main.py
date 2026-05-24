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
from datetime import datetime
from dotenv import load_dotenv

from src.scanner import TokenScanner
from src.rug_checker import RugChecker
from src.chart_analyzer import ChartAnalyzer
from src.trade_executor import TradeExecutor
from src.telegram_bot import TradeBot
from src.logger import logger

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
        self.max_age = int(os.getenv('MAX_AGE_MINUTES', 60))
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
            executor=self.executor
        ) if self.telegram_token else None
        
        # State
        self.running = True
        self.scanned_mints = set()  # Avoid re-scanning same tokens
        self.active_positions = {}
        
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
        
        # Filter out already scanned
        new_tokens = [t for t in trending if t['mint'] not in self.scanned_mints]
        
        for token in new_tokens:
            if not self.running:
                break
            
            mint = token['mint']
            self.scanned_mints.add(mint)
            
            logger.info(f"Analyzing {token['symbol']} ({mint[:8]}...)")
            
            # Stage 2: Rug check
            rug_result = self.rug_checker.check_token(mint)
            
            if rug_result['is_likely_scam']:
                logger.info(f"  Scam filter: SKIPPED ({rug_result['risk_score']}/100)")
                continue
            
            # Stage 3: Chart analysis
            chart_result = self.chart_analyzer.analyze_token_chart(mint)
            
            if chart_result['signal'] not in ['BUY', 'STRONG_BUY']:
                logger.info(f"  Chart filter: SKIPPED ({chart_result['signal']})")
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
                continue
            
            # Stage 5: Send Telegram alert
            if self.bot:
                trade_id = await self.bot.send_alert(plan)
                if trade_id:
                    logger.info(f"  Alert sent! Trade ID: {trade_id}")
                    # Wait a bit for user to respond
                    await asyncio.sleep(5)
            else:
                logger.info(f"  Trade ready (no Telegram configured): {token['symbol']}")
                logger.info(f"  {plan}")
    
    async def run(self):
        """Main bot loop."""
        logger.info("=" * 60)
        logger.info("GEM TRADER BOT STARTING")
        logger.info("=" * 60)
        
        # Start Telegram bot polling if configured
        polling_task = None
        if self.bot:
            logger.info("Starting Telegram bot...")
            self.bot.register_handlers()
            await self.bot.app.initialize()
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
            await self._shutdown(polling_task)
    
    async def _run_telegram_polling(self):
        """Run Telegram polling in background."""
        try:
            await self.bot.app.updater.start_polling(read_timeout=30)
        except Exception as e:
            logger.error(f"Telegram polling error: {e}")
    
    async def _shutdown(self, polling_task=None):
        """Graceful shutdown."""
        logger.info("Shutting down...")
        self.running = False
        
        if polling_task:
            polling_task.cancel()
        
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
