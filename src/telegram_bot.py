"""
Telegram bot module for user interactions.
Sends trade alerts, handles approvals, monitors positions.
"""

import logging
import asyncio
from typing import Dict, Optional, Callable, Any
from datetime import datetime, timedelta
import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ContextTypes, JobQueue
)

logger = logging.getLogger(__name__)


class TradeBot:
    """
    Telegram bot for gem trader.
    Sends alerts, handles approve/skip decisions, monitors positions.
    """
    
    def __init__(self, token: str, chat_id: str, executor=None, scanner=None, rug_checker=None, chart_analyzer=None):
        """
        Initialize the Telegram bot.
        
        Args:
            token: Telegram bot token from @BotFather
            chat_id: Target chat ID for alerts
            executor: Optional trade executor instance
            scanner: Token scanner for fetching token info
            rug_checker: RugChecker for scam analysis
            chart_analyzer: ChartAnalyzer for technical signals
        """
        self.token = token
        self.chat_id = chat_id
        self.executor = executor
        self.scanner = scanner
        self.rug_checker = rug_checker
        self.chart_analyzer = chart_analyzer
        self.app = Application.builder().token(token).build()
        
        # Track pending trades and positions
        self.pending_approvals = {}  # trade_id -> trade details
        self.active_positions = {}   # token_mint -> position details
        self.next_trade_id = 0
        
        # Callbacks
        self.on_approve_callback = None
        self.on_skip_callback = None
        self.on_sell_callback = None
        self.on_trade_callback = None  # Called when user confirms a trade
    
    def register_handlers(self):
        """Register command and callback handlers."""
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("positions", self._cmd_positions))
        self.app.add_handler(CommandHandler("stop", self._cmd_stop))
        self.app.add_handler(CommandHandler("check", self._cmd_check))
        self.app.add_handler(CommandHandler("trade", self._cmd_trade))
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))
    
    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        await update.message.reply_text(
            "Gem Trader Bot is running!\n\n"
            "Commands:\n"
            "/check <address> - Analyze a token contract\n"
            "/trade <address> [amount_sol] - Open a trade with auto TP/SL\n"
            "/status - Bot status and recent activity\n"
            "/positions - View open positions\n"
            "/stop - Stop monitoring\n"
        )
    
    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        text = (
            "Bot Status: RUNNING\n"
            f"Pending approvals: {len(self.pending_approvals)}\n"
            f"Active positions: {len(self.active_positions)}\n"
            f"Last scan: {datetime.now().strftime('%H:%M:%S')}\n"
        )
        await update.message.reply_text(text)
    
    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /positions command."""
        if not self.active_positions:
            await update.message.reply_text("No open positions.")
            return
        
        text = "Open Positions:\n\n"
        for mint, pos in self.active_positions.items():
            text += (
                f"Token: {pos.get('symbol', mint[:8])}\n"
                f"Entry: ${pos['entry_price']:.8f}\n"
                f"Current: ${pos.get('current_price', 0):.8f}\n"
                f"P&L: {pos.get('pnl_pct', 0):+.2f}%\n"
                f"Sell: /sell_{mint}\n\n"
            )
        await update.message.reply_text(text)
    
    async def _cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stop command."""
        await update.message.reply_text("Stopping bot...")
        # Signal the main loop to stop
        if self.on_stop_callback:
            await self.on_stop_callback()
    
    async def _cmd_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /check <address> command."""
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /check <token_mint_address>")
            return

        mint = args[0]
        await update.message.reply_text(f"Analyzing {mint[:8]}... (this may take ~10s)")

        try:
            # Fetch token info from DexScreener
            token_info = None
            if self.scanner:
                token_info = self.scanner.get_token_info(mint)

            # Run rug check
            rug = None
            if self.rug_checker:
                rug = self.rug_checker.check_token(mint)

            # Chart analysis (only with price data)
            chart = None
            if self.chart_analyzer:
                chart = self.chart_analyzer.analyze_token_chart(mint, token_data=token_info)

            # Build response
            lines = [f"Token: {mint}"]

            if token_info:
                lines.append(f"Symbol: {token_info.get('symbol', '?')}")
                lines.append(f"Price: ${token_info.get('price_usd', 0):.8f}")
                lines.append(f"Market Cap: ${float(token_info.get('market_cap_usd', 0)):,.0f}")
                lines.append(f"Liquidity: ${token_info.get('liquidity_usd', 0):,.0f}")
                lines.append(f"Vol 5m: ${float(token_info.get('volume_5m_usd', 0)):,.0f}")
                lines.append(f"Vol 1h: ${float(token_info.get('volume_1h_usd', 0)):,.0f}")
            else:
                lines.append("Token info: Not found on DexScreener")

            if rug:
                score = rug['risk_score']
                emoji = "LOW" if score < 30 else "MEDIUM" if score < 60 else "HIGH"
                lines.append(f"Rug Risk: {emoji} ({score}/100)")
                lines.append(f"Mint auth renounced: {'YES' if rug['checks']['mint_authority_renounced'] else 'NO'}")
                lines.append(f"Freeze auth: {'YES (RISK!)' if rug['checks']['freeze_authority_present'] else 'none'}")
                conc = rug['checks'].get('holder_concentration', 0)
                lines.append(f"Top 10 holders: {conc:.1%}")

            if chart:
                lines.append(f"Signal: {chart['signal']} (confidence: {chart['score']}/100)")
                lines.append(f"RSI(14): {chart['rsi']:.1f}")

            body = "```\n" + "\n".join(lines) + "\n```"

            # Only show trade buttons if we have token info and rug check
            if token_info and rug and rug['risk_score'] < 60:
                keyboard = [
                    [
                        InlineKeyboardButton("TRADE 0.05 SOL", callback_data=f"trade_quick:{mint}:0.05"),
                        InlineKeyboardButton("TRADE 0.1 SOL", callback_data=f"trade_quick:{mint}:0.1"),
                    ],
                    [InlineKeyboardButton("CUSTOM AMOUNT", callback_data=f"trade_custom:{mint}")],
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(body, parse_mode='Markdown', reply_markup=reply_markup)
            else:
                await update.message.reply_text(body, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Error in /check: {e}")
            await update.message.reply_text(f"Error analyzing token: {e}")

    async def _cmd_trade(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /trade <mint> [amount_sol] command."""
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /trade <token_mint_address> [amount_sol]")
            return

        mint = args[0]
        amount_sol = float(args[1]) if len(args) > 1 and args[1] else 0.05

        if not self.scanner or not self.rug_checker or not self.chart_analyzer:
            await update.message.reply_text("Bot modules not loaded.")
            return

        await update.message.reply_text(f"Analyzing {mint[:8]} for trade setup...")

        try:
            token_info = self.scanner.get_token_info(mint)
            if not token_info:
                await update.message.reply_text("Token not found on DexScreener.")
                return

            rug = self.rug_checker.check_token(mint)
            if rug['is_likely_scam']:
                await update.message.reply_text(f"Scam risk too high ({rug['risk_score']}/100). Trade cancelled.")
                return

            chart = self.chart_analyzer.analyze_token_chart(mint, token_data=token_info)

            msg = (
                f"Trade Setup: {token_info.get('symbol', mint[:8])}\n"
                f"Entry: ${token_info['price_usd']:.8f}\n"
                f"Amount: {amount_sol:.4f} SOL\n"
                f"Risk: {rug['risk_score']}/100\n"
                f"Signal: {chart['signal']} ({chart['score']}/100)\n\n"
                f"TP1: +50% → sell 30%\n"
                f"TP2: +100% → sell 30%\n"
                f"TP3: +200% → sell 40%\n"
                f"SL: -20% → sell 100%\n"
                f"(Use env vars TP1_PERCENT, TP2_PERCENT, etc. to customize)"
            )

            keyboard = [
                [
                    InlineKeyboardButton("CONFIRM TRADE", callback_data=f"trade_confirm:{mint}:{amount_sol}"),
                    InlineKeyboardButton("CANCEL", callback_data=f"trade_cancel:{mint}"),
                ]
            ]
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

        except Exception as e:
            logger.error(f"Error in /trade: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def _handle_trade_confirm(self, query, parts):
        """Handle trade confirmation callback."""
        # parts = [action, "mint:amount_sol"]
        value = parts[1] if len(parts) > 1 else ""
        sub = value.split(":")
        mint = sub[0]
        amount_sol = float(sub[1]) if len(sub) > 1 else 0.05

        if not self.on_trade_callback:
            await query.edit_message_text("Trade execution not configured.")
            return

        # Run analysis and create position
        success, msg = await self.on_trade_callback(mint, amount_sol)
        if success:
            await query.edit_message_text(msg)
        else:
            await query.edit_message_text(f"Trade failed: {msg}")

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle inline keyboard button presses.
        
        Callback data format:
            approve:<trade_id>
            skip:<trade_id>
            sell:<mint>
            trade_quick:<mint>:<amount_sol>
            trade_confirm:<mint>:<amount_sol>
            trade_custom:<mint>
            trade_cancel:<mint>
        """
        query = update.callback_query
        await query.answer()
        
        data = query.data
        parts = data.split(":", 1)
        action = parts[0]
        value = parts[1] if len(parts) > 1 else ""
        
        if action == "approve":
            await self._handle_approve(query, value)
        elif action == "skip":
            await self._handle_skip(query, value)
        elif action == "sell":
            await self._handle_sell(query, value)
        elif action in ("trade_quick", "trade_confirm"):
            await self._handle_trade_confirm(query, parts)
        elif action == "trade_custom":
            await query.edit_message_text(
                f"Send amount in SOL as a reply, or use:\n"
                f"/trade {value} <amount_sol>"
            )
        elif action == "trade_cancel":
            await query.edit_message_text("Trade cancelled.")
        else:
            logger.warning(f"Unknown callback action: {action}")
    
    async def _handle_approve(self, query, trade_id: str):
        """Handle user approving a trade."""
        if trade_id not in self.pending_approvals:
            await query.edit_message_text("Trade no longer available (expired).")
            return
        
        trade = self.pending_approvals.pop(trade_id)
        
        # Execute the trade
        if self.on_approve_callback:
            success = await self.on_approve_callback(trade)
            if success:
                await query.edit_message_text(
                    f"[OK] Trade EXECUTED: {trade.get('symbol', 'Unknown')}\n"
                    f"Amount: {trade.get('amount_sol', 0):.4f} SOL\n"
                    f"TX: {trade.get('tx_signature', 'pending')}"
                )
            else:
                await query.edit_message_text(
                    f"[!] Trade FAILED: {trade.get('symbol', 'Unknown')}\n"
                    f"Please check logs."
                )
        else:
            await query.edit_message_text("No executor configured. Trade skipped.")
    
    async def _handle_skip(self, query, trade_id: str):
        """Handle user skipping a trade."""
        if trade_id in self.pending_approvals:
            trade = self.pending_approvals.pop(trade_id)
            logger.info(f"Trade skipped by user: {trade.get('symbol')}")
        
        await query.edit_message_text(
            f"[SKIP] Trade cancelled.\n"
            f"Bot will continue scanning for opportunities."
        )
        
        if self.on_skip_callback:
            await self.on_skip_callback(trade_id)
    
    async def _handle_sell(self, query, mint: str):
        """Handle user requesting to sell a position."""
        if mint not in self.active_positions:
            await query.edit_message_text("Position not found.")
            return
        
        position = self.active_positions[mint]
        
        if self.on_sell_callback:
            success = await self.on_sell_callback(mint)
            if success:
                del self.active_positions[mint]
                await query.edit_message_text(
                    f"[OK] Sold {position.get('symbol', mint[:8])}"
                )
            else:
                await query.edit_message_text(
                    f"[!] Sell failed for {position.get('symbol', mint[:8])}"
                )
    
    async def send_alert(self, trade_plan: Dict) -> Optional[str]:
        """
        Send trade alert to user for approval.
        
        Args:
            trade_plan: Execution plan dict from TradeExecutor
        
        Returns:
            Trade ID if sent, None if failed
        """
        trade_id = str(self.next_trade_id)
        self.next_trade_id += 1
        
        # Build message
        risk_emoji = "HIGH" if trade_plan.get('rug_score', 0) > 50 else "LOW"
        signal_emoji = trade_plan.get('chart_signal', 'HOLD')
        
        text = (
            f"Token: {trade_plan.get('token', 'Unknown')}\n"
            f"Price: ${trade_plan.get('token_price', 0):.8f}\n"
            f"Risk: {risk_emoji} ({trade_plan.get('rug_score', 0)}/100)\n"
            f"Signal: {signal_emoji} ({trade_plan.get('chart_confidence', 0)}/100)\n"
            f"Volume: ${trade_plan.get('risk_usd', 0):.2f}\n\n"
            f"Buy: {trade_plan.get('buy_amount_sol', 0):.4f} SOL\n"
            f"TP: {trade_plan.get('take_profit_target', 0):.8f}\n"
            f"SL: {trade_plan.get('stop_loss_level', 0):.8f}"
        )
        
        # Build keyboard
        keyboard = [
            [
                InlineKeyboardButton("BUY", callback_data=f"approve:{trade_id}"),
                InlineKeyboardButton("SKIP", callback_data=f"skip:{trade_id}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=reply_markup,
            )
            
            # Store pending approval
            self.pending_approvals[trade_id] = {
                **trade_plan,
                'trade_id': trade_id,
                'sent_at': datetime.now().isoformat(),
            }
            
            logger.info(f"Alert sent for {trade_plan.get('token')} (ID: {trade_id})")
            return trade_id
            
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")
            return None
    
    async def send_notification(self, message: str):
        """
        Send a simple text notification (no buttons).
        
        Args:
            message: Text message to send
        """
        try:
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
            )
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
    
    async def send_position_update(self, mint: str, pnl_info: Dict):
        """
        Send position P&L update.
        
        Args:
            mint: Token mint address
            pnl_info: P&L info dict from TradeExecutor
        """
        symbol = pnl_info.get('token_mint', mint)[:8]
        pnl_pct = pnl_info.get('unrealized_pnl_pct', 0)
        pnl_usd = pnl_info.get('unrealized_pnl_usd', 0)
        
        emoji = "PROFIT" if pnl_pct > 0 else "LOSS"
        action = pnl_info.get('action', 'UPDATE')
        
        text = (
            f"Position: {symbol}\n"
            f"P&L: {emoji} {pnl_pct:+.2f}% (${pnl_usd:+.2f})\n"
            f"Action: {action}"
        )
        
        keyboard = None
        if action in ['TAKE_PROFIT', 'STOP_LOSS']:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("SELL NOW", callback_data=f"sell:{mint}"),
            ]])
        
        try:
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Failed to send position update: {e}")
    
    async def run_polling(self):
        """Start the bot in polling mode."""
        logger.info("Starting Telegram bot polling...")
        self.register_handlers()
        
        # Set up job queue for periodic checks
        job_queue = self.app.job_queue
        
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)
    
    async def stop(self):
        """Stop the bot gracefully."""
        logger.info("Stopping Telegram bot...")
        await self.app.stop()
        await self.app.shutdown()


# Test
if __name__ == "__main__":
    import asyncio
    from dotenv import load_dotenv
    
    load_dotenv()
    
    logging.basicConfig(level=logging.INFO)
    
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    if not bot_token or not chat_id:
        print("[!] Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        exit(1)
    
    print("\n" + "="*70)
    print("TELEGRAM BOT TEST")
    print("="*70)
    
    async def test():
        bot = TradeBot(bot_token, chat_id)
        
        # Send test alert
        test_trade = {
            'token': 'BONK',
            'token_price': 0.00000602,
            'rug_score': 35,
            'chart_signal': 'BUY',
            'chart_confidence': 65,
            'buy_amount_sol': 0.05,
            'risk_usd': 7.50,
            'take_profit_target': 0.00000903,
            'stop_loss_level': 0.00000482,
        }
        
        print("\nSending test alert...")
        trade_id = await bot.send_alert(test_trade)
        
        if trade_id:
            print(f"[OK] Alert sent! Trade ID: {trade_id}")
            print("\nCheck your Telegram for the alert.")
            print("Start polling to handle button presses...")
            
            # Don't actually poll in test - just initialize
            await bot.app.initialize()
            await bot.stop()
        else:
            print("[!] Failed to send alert")
    
    asyncio.run(test())
