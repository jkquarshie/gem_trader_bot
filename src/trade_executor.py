"""
Trade execution module for Solana token swaps.
Uses Jupiter API for best price routing and execution.
Manages wallet balances and position sizing.
"""

import logging
import requests
from typing import Dict, Optional, List, Tuple
from datetime import datetime
import json
import time
import base64
import os

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solana.rpc.api import Client as SolanaClient
from solana.rpc.types import TxOpts

logger = logging.getLogger(__name__)


class TradeExecutor:
    """
    Executes trades on Solana via Jupiter API.
    Handles swap routing, slippage protection, and position management.
    """
    
    # Jupiter API endpoints
    JUPITER_API = "https://api.jup.ag/swap/info"
    JUPITER_PRICE_API = "https://price.jup.ag/v4"
    JUPITER_QUOTE_API = "https://quote-api.jup.ag/v6/quote"
    JUPITER_SWAP_API = "https://quote-api.jup.ag/v6/swap"
    
    # Solana constants
    SOL_DECIMALS = 9
    USDC_MINT = "EPjFWaLb3hyccuBY4fgkQK9fu2TWrKSLBqqsCNCvuGjP"
    WRAPPED_SOL = "So11111111111111111111111111111111111111112"
    
    def __init__(self, rpc_endpoint: str, wallet_address: str = None, keypair: Keypair = None):
        """
        Initialize trade executor.
        
        Args:
            rpc_endpoint: Solana RPC endpoint
            wallet_address: User's wallet address (optional, for read-only mode)
            keypair: Solana Keypair for signing transactions
        """
        self.rpc_endpoint = rpc_endpoint
        self.wallet_address = wallet_address
        self.keypair = keypair
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'GemTraderBot/1.0'})
        self.solana_client = SolanaClient(rpc_endpoint) if rpc_endpoint else None
        
        # Track positions
        self.positions = {}  # mint -> {amount, entry_price, entry_time}
    
    def get_swap_quote(self, input_mint: str, output_mint: str, amount_in: int, slippage_bps: int = 50) -> Optional[Dict]:
        """
        Get swap quote from Jupiter.
        
        Args:
            input_mint: Input token mint address
            output_mint: Output token mint address
            amount_in: Amount to swap (in smallest units)
            slippage_bps: Slippage in basis points (50 = 0.5%)
        
        Returns:
            Quote dict with: input_amount, output_amount, route_plan, etc.
        """
        logger.info(f"Getting quote: {amount_in} of {input_mint[:8]}... -> {output_mint[:8]}...")
        
        try:
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount_in,
                "slippageBps": slippage_bps,
            }
            
            response = self.session.get(self.JUPITER_QUOTE_API, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if 'data' not in data or not data['data']:
                logger.warning("No quote data received")
                return None
            
            quote = data['data'][0] if isinstance(data['data'], list) else data['data']
            
            # Extract key info
            input_amount = int(quote.get('inAmount', 0))
            output_amount = int(quote.get('outAmount', 0))
            price_impact = float(quote.get('priceImpactPct', 0))
            
            logger.info(f"Quote: {output_amount} output tokens (impact: {price_impact:.2f}%)")
            
            return {
                'input_mint': input_mint,
                'output_mint': output_mint,
                'input_amount': input_amount,
                'output_amount': output_amount,
                'price_impact_pct': price_impact,
                'slippage_bps': slippage_bps,
                'route': quote.get('routePlan', []),
                'quoted_at': datetime.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Error getting quote: {e}")
            return None
    
    def get_token_prices(self, mint_addresses: List[str]) -> Dict[str, float]:
        """
        Get current prices for tokens.
        
        Args:
            mint_addresses: List of token mint addresses
        
        Returns:
            Dict mapping mint -> price in USD
        """
        logger.info(f"Fetching prices for {len(mint_addresses)} tokens")
        
        prices = {}
        
        try:
            # Batch price queries
            for mint in mint_addresses:
                try:
                    params = {"ids": mint}
                    response = self.session.get(self.JUPITER_PRICE_API, params=params, timeout=5)
                    response.raise_for_status()
                    data = response.json()
                    
                    if 'data' in data and mint in data['data']:
                        price = float(data['data'][mint].get('price', 0))
                        prices[mint] = price
                        logger.debug(f"  {mint[:8]}...: ${price:.8f}")
                    
                except Exception as e:
                    logger.debug(f"Error fetching price for {mint}: {e}")
                    prices[mint] = 0
            
            return prices
            
        except Exception as e:
            logger.error(f"Error fetching token prices: {e}")
            return {}
    
    def estimate_buy_amount(self, wallet_balance_sol: float, risk_percentage: float = 5.0) -> Tuple[int, float]:
        """
        Calculate buy amount based on wallet balance and risk tolerance.
        
        Args:
            wallet_balance_sol: Wallet balance in SOL
            risk_percentage: Percentage of wallet to risk per trade (5 = 5%)
        
        Returns:
            Tuple of (amount_in_lamports, risk_usd)
        """
        try:
            # Risk amount in SOL
            risk_sol = wallet_balance_sol * (risk_percentage / 100)
            
            # Convert to lamports
            risk_lamports = int(risk_sol * (10 ** self.SOL_DECIMALS))
            
            logger.info(f"Buy sizing: {risk_sol:.4f} SOL ({risk_percentage}% of wallet)")
            
            # Assume ~$150 per SOL for USD estimate
            risk_usd = risk_sol * 150
            
            return (risk_lamports, risk_usd)
            
        except Exception as e:
            logger.error(f"Error calculating buy amount: {e}")
            return (0, 0)
    
    def validate_swap_safety(self, quote: Dict, max_price_impact: float = 10.0, max_slippage_bps: int = 200) -> Tuple[bool, str]:
        """
        Validate that a swap is safe to execute.
        
        Args:
            quote: Quote data from get_swap_quote
            max_price_impact: Maximum acceptable price impact (%)
            max_slippage_bps: Maximum acceptable slippage (basis points)
        
        Returns:
            Tuple of (is_safe, reason)
        """
        logger.info("Validating swap safety...")
        
        try:
            price_impact = quote.get('price_impact_pct', 0)
            slippage = quote.get('slippage_bps', 0)
            
            # Check price impact
            if abs(price_impact) > max_price_impact:
                return (False, f"Price impact too high: {price_impact:.2f}%")
            
            # Check slippage
            if slippage > max_slippage_bps:
                return (False, f"Slippage too high: {slippage} bps")
            
            # Check output amount
            if quote.get('output_amount', 0) == 0:
                return (False, "Zero output amount")
            
            logger.info("[OK] Swap is safe to execute")
            return (True, "")
            
        except Exception as e:
            logger.error(f"Error validating swap: {e}")
            return (False, f"Validation error: {e}")
    
    def test_honeypot(self, token_mint: str, test_amount_lamports: int = 1000000) -> Tuple[bool, str]:
        """
        Test if token is a honeypot by simulating a small swap.
        
        Args:
            token_mint: Token to test
            test_amount_lamports: Small test amount in SOL (default: 0.001 SOL)
        
        Returns:
            Tuple of (is_tradable, reason)
        """
        logger.info(f"Testing honeypot for {token_mint}")
        
        try:
            # Get quote: SOL -> token
            quote_buy = self.get_swap_quote(
                self.WRAPPED_SOL,
                token_mint,
                test_amount_lamports,
                slippage_bps=100
            )
            
            if not quote_buy or quote_buy.get('output_amount', 0) == 0:
                return (False, "Cannot buy token (no liquidity or sandwiched)")
            
            # Get quote: token -> SOL (simulate sell)
            quote_sell = self.get_swap_quote(
                token_mint,
                self.WRAPPED_SOL,
                quote_buy['output_amount'],
                slippage_bps=100
            )
            
            if not quote_sell or quote_sell.get('output_amount', 0) == 0:
                return (False, "Cannot sell token (honeypot detected)")
            
            # Check if we can recover at least 50% of input
            recovery_ratio = quote_sell['output_amount'] / test_amount_lamports
            if recovery_ratio < 0.5:
                return (False, f"High sell tax detected (only {recovery_ratio*100:.1f}% recoverable)")
            
            logger.info(f"[OK] Token appears tradable (recovery: {recovery_ratio*100:.1f}%)")
            return (True, "")
            
        except Exception as e:
            logger.error(f"Error testing honeypot: {e}")
            return (False, f"Test failed: {e}")
    
    def execute_swap(self, input_mint: str, output_mint: str, amount_lamports: int,
                     slippage_bps: int = 100) -> Optional[Dict]:
        """
        Execute a swap via Jupiter: get quote → sign transaction → send to RPC.

        Returns tx result dict with signature, or None on failure.
        """
        if not self.keypair:
            logger.warning("No keypair configured — swap not executed")
            return None

        try:
            # Step 1: Get quote
            quote = self.get_swap_quote(input_mint, output_mint, amount_lamports, slippage_bps)
            if not quote:
                return None

            # Step 2: Request swap transaction from Jupiter
            user_pk = str(self.keypair.pubkey())
            payload = {
                "quoteResponse": quote.get('route', {}),
                "userPublicKey": user_pk,
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto",
            }
            # The quote object from get_swap_quote doesn't match Jupiter's expected format
            # We need to re-fetch the raw quote for the swap endpoint
            raw_params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount_lamports,
                "slippageBps": slippage_bps,
            }
            raw_resp = self.session.get(self.JUPITER_QUOTE_API, params=raw_params, timeout=10)
            raw_resp.raise_for_status()
            raw_quote = raw_resp.json()

            if 'data' not in raw_quote or not raw_quote['data']:
                logger.warning("No quote data for swap")
                return None

            # Take first route
            quote_response = raw_quote['data'][0] if isinstance(raw_quote['data'], list) else raw_quote['data']

            swap_payload = {
                "quoteResponse": quote_response,
                "userPublicKey": user_pk,
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto",
            }

            swap_resp = self.session.post(self.JUPITER_SWAP_API, json=swap_payload, timeout=15)
            swap_resp.raise_for_status()
            swap_data = swap_resp.json()

            tx_b64 = swap_data.get('swapTransaction')
            if not tx_b64:
                logger.warning("No swap transaction in response")
                return None

            # Step 3: Deserialize and sign
            tx_bytes = base64.b64decode(tx_b64)
            tx = VersionedTransaction.from_bytes(tx_bytes)
            signature = self.keypair.sign_message(tx.message.serialize())
            signed_tx = VersionedTransaction.populate(tx.message, [signature])

            # Step 4: Send to RPC
            if not self.solana_client:
                logger.warning("No RPC client configured")
                return None

            tx_opts = TxOpts(skip_preflight=False, max_retries=3)
            result = self.solana_client.send_raw_transaction(
                txn=bytes(signed_tx),
                opts=tx_opts,
            )
            tx_sig = result.value if hasattr(result, 'value') else str(result)

            logger.info(f"[OK] Swap executed: {tx_sig}")
            return {
                'signature': str(tx_sig),
                'input_amount': amount_lamports,
                'output_amount': quote_response.get('outAmount', 0),
                'executed_at': datetime.now().isoformat(),
                'user_public_key': user_pk,
            }

        except Exception as e:
            logger.error(f"Swap execution failed: {e}")
            return None

    def create_position(self, token_info: Dict, chart_result: Dict = None,
                        buy_amount_sol: float = 0.05, entry_price: float = None,
                        tp1_pct: float = 50.0, tp1_sell_pct: float = 30.0,
                        tp2_pct: float = 100.0, tp2_sell_pct: float = 30.0,
                        tp3_pct: float = 200.0, tp3_sell_pct: float = 40.0,
                        stop_loss_pct: float = 20.0) -> Dict:
        """
        Create a tracked position with multi-level TP/SL.

        Auto-calculates entry price from current price or chart support level.
        """
        if entry_price is None or entry_price <= 0:
            entry_price = token_info.get('price_usd', 0)
            # Use chart support as a more conservative entry if available
            if chart_result and chart_result.get('support', 0) > 0:
                entry_price = min(entry_price, chart_result['support'])

        mint = token_info['mint']
        total_tokens = int(buy_amount_sol / entry_price) if entry_price > 0 else 0

        position = {
            'mint': mint,
            'symbol': token_info.get('symbol', mint[:8]),
            'entry_price': entry_price,
            'total_tokens': total_tokens,
            'remaining_tokens': total_tokens,
            'total_cost_sol': buy_amount_sol,
            'entry_time': datetime.now().isoformat(),
            'status': 'ACTIVE',
            'tp_levels': [
                {'level': 1, 'pct': tp1_pct, 'sell_pct': tp1_sell_pct,
                 'triggered': False, 'sold_at': None, 'sold_price': None},
                {'level': 2, 'pct': tp2_pct, 'sell_pct': tp2_sell_pct,
                 'triggered': False, 'sold_at': None, 'sold_price': None},
                {'level': 3, 'pct': tp3_pct, 'sell_pct': tp3_sell_pct,
                 'triggered': False, 'sold_at': None, 'sold_price': None},
            ],
            'stop_loss_pct': stop_loss_pct,
            'stop_loss_triggered': False,
            'simulated': True,
        }

        self.positions[mint] = position
        logger.info(f"Position created: {buy_amount_sol:.4f} SOL of {token_info['symbol']} at ${entry_price:.8f}")
        return position

    def check_tp_sl(self, mint: str, current_price: float) -> Optional[Dict]:
        """
        Check active positions for TP/SL triggers.

        Returns dict with triggered actions if any, else None.
        """
        position = self.positions.get(mint)
        if not position or position['status'] not in ('ACTIVE', 'PARTIAL'):
            return None

        actions = []
        pnl_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100

        # Check stop loss
        if not position.get('stop_loss_triggered'):
            sl_pct = position.get('stop_loss_pct', 20)
            if pnl_pct <= -sl_pct:
                position['stop_loss_triggered'] = True
                position['remaining_tokens'] = 0
                position['status'] = 'CLOSED'
                actions.append({
                    'type': 'STOP_LOSS',
                    'pnl_pct': pnl_pct,
                    'sell_tokens': position['total_tokens'],
                })

        # Check TP levels (in order)
        if not actions:
            for tp in position['tp_levels']:
                if tp['triggered']:
                    continue
                if pnl_pct >= tp['pct']:
                    tp['triggered'] = True
                    tp['sold_at'] = datetime.now().isoformat()
                    tp['sold_price'] = current_price

                    sell_tokens = int(position['total_tokens'] * tp['sell_pct'] / 100)
                    position['remaining_tokens'] -= sell_tokens
                    if position['remaining_tokens'] <= 0:
                        position['remaining_tokens'] = 0
                        position['status'] = 'CLOSED'
                    elif position['status'] == 'ACTIVE':
                        position['status'] = 'PARTIAL'

                    actions.append({
                        'type': f'TP{tp["level"]}',
                        'pnl_pct': pnl_pct,
                        'sell_pct': tp['sell_pct'],
                        'sell_tokens': sell_tokens,
                        'remaining_pct': (position['remaining_tokens'] / position['total_tokens'] * 100)
                        if position['total_tokens'] > 0 else 0,
                    })

        if actions:
            result = {
                'mint': position['mint'],
                'symbol': position['symbol'],
                'entry_price': position['entry_price'],
                'current_price': current_price,
                'pnl_pct': pnl_pct,
                'actions': actions,
                'remaining_tokens': position['remaining_tokens'],
                'status': position['status'],
                'simulated': position.get('simulated', True),
            }
            logger.info(f"TP/SL triggered for {position['symbol']}: {[a['type'] for a in actions]}")
            return result

        return None

    def record_position(self, token_mint: str, amount: int, entry_price: float):
        """Record a bought position."""
        self.positions[token_mint] = {
            'amount': amount,
            'entry_price': entry_price,
            'entry_time': datetime.now().isoformat(),
        }
        logger.info(f"Position recorded: {amount} tokens at ${entry_price:.8f}")
    
    def calculate_pnl(self, token_mint: str, current_price: float) -> Optional[Dict]:
        """
        Calculate P&L for an open position.
        
        Args:
            token_mint: Token mint address
            current_price: Current token price in USD
        
        Returns:
            Dict with: entry_price, current_price, amount, unrealized_pnl_pct, unrealized_pnl_usd
        """
        if token_mint not in self.positions:
            logger.warning(f"No position found for {token_mint}")
            return None
        
        try:
            pos = self.positions[token_mint]
            entry_price = pos['entry_price']
            amount = pos['amount']
            
            # Calculate P&L
            pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
            pnl_usd = (current_price - entry_price) * amount
            
            result = {
                'token_mint': token_mint,
                'entry_price': entry_price,
                'current_price': current_price,
                'amount': amount,
                'unrealized_pnl_pct': pnl_pct,
                'unrealized_pnl_usd': pnl_usd,
                'entry_time': pos['entry_time'],
            }
            
            logger.info(f"P&L: {pnl_pct:+.2f}% (${pnl_usd:+.2f})")
            return result
            
        except Exception as e:
            logger.error(f"Error calculating P&L: {e}")
            return None
    
    def check_exit_condition(self, token_mint: str, current_price: float, 
                            take_profit_pct: float = 50.0, stop_loss_pct: float = 20.0) -> Optional[str]:
        """
        Check if position should be exited based on P&L targets.
        
        Args:
            token_mint: Token mint address
            current_price: Current price
            take_profit_pct: Take profit target (%)
            stop_loss_pct: Stop loss limit (%)
        
        Returns:
            "TAKE_PROFIT", "STOP_LOSS", or None
        """
        pnl = self.calculate_pnl(token_mint, current_price)
        if not pnl:
            return None
        
        unrealized_pnl = pnl['unrealized_pnl_pct']
        
        if unrealized_pnl >= take_profit_pct:
            logger.warning(f"Take profit hit: {unrealized_pnl:.2f}% >= {take_profit_pct}%")
            return "TAKE_PROFIT"
        
        if unrealized_pnl <= -stop_loss_pct:
            logger.warning(f"Stop loss hit: {unrealized_pnl:.2f}% <= -{stop_loss_pct}%")
            return "STOP_LOSS"
        
        return None
    
    def get_execution_plan(self, token_info: Dict, rug_result: Dict, chart_result: Dict, 
                          wallet_balance_sol: float, risk_pct: float = 5.0) -> Optional[Dict]:
        """
        Generate full trade execution plan.
        
        Args:
            token_info: Token data from scanner
            rug_result: Rug check results
            chart_result: Chart analysis results
            wallet_balance_sol: Wallet balance in SOL
            risk_pct: Risk percentage per trade
        
        Returns:
            Execution plan dict or None if trade should be skipped
        """
        logger.info(f"Generating execution plan for {token_info['symbol']}")
        
        try:
            token_mint = token_info['mint']
            
            # Filter 1: Risk check
            if rug_result['risk_score'] > 60:
                return {
                    'token': token_info['symbol'],
                    'decision': 'SKIP',
                    'reason': f"High rug risk: {rug_result['risk_score']}/100",
                }
            
            # Filter 2: Chart signal
            if chart_result['signal'] not in ['BUY', 'STRONG_BUY']:
                return {
                    'token': token_info['symbol'],
                    'decision': 'SKIP',
                    'reason': f"Unfavorable chart signal: {chart_result['signal']}",
                }
            
            # Filter 3: Honeypot check
            is_tradable, honeypot_msg = self.test_honeypot(token_mint)
            if not is_tradable:
                return {
                    'token': token_info['symbol'],
                    'decision': 'SKIP',
                    'reason': f"Honeypot detected: {honeypot_msg}",
                }
            
            # All checks passed - generate buy plan
            amount_lamports, risk_usd = self.estimate_buy_amount(wallet_balance_sol, risk_pct)
            
            # Get swap quote
            quote = self.get_swap_quote(
                self.WRAPPED_SOL,
                token_mint,
                amount_lamports,
                slippage_bps=100
            )
            
            if not quote:
                return {
                    'token': token_info['symbol'],
                    'decision': 'SKIP',
                    'reason': "Could not get swap quote",
                }
            
            # Validate swap
            is_safe, safety_msg = self.validate_swap_safety(quote)
            if not is_safe:
                return {
                    'token': token_info['symbol'],
                    'decision': 'SKIP',
                    'reason': f"Swap not safe: {safety_msg}",
                }
            
            # All checks passed - ready to execute
            plan = {
                'token': token_info['symbol'],
                'decision': 'EXECUTE',
                'token_mint': token_mint,
                'token_price': token_info['price_usd'],
                'buy_amount_sol': amount_lamports / (10 ** self.SOL_DECIMALS),
                'buy_amount_lamports': amount_lamports,
                'risk_usd': risk_usd,
                'output_tokens': quote['output_amount'],
                'output_price': token_info['price_usd'],
                'price_impact_pct': quote['price_impact_pct'],
                'entry_price': token_info['price_usd'],
                'take_profit_target': token_info['price_usd'] * 1.5,  # 50% profit
                'stop_loss_level': token_info['price_usd'] * 0.8,     # 20% loss
                'rug_score': rug_result['risk_score'],
                'chart_signal': chart_result['signal'],
                'chart_confidence': chart_result['score'],
                'generated_at': datetime.now().isoformat(),
            }
            
            logger.info(f"[OK] Execution plan ready: Buy {amount_lamports/1e9:.4f} SOL of {token_info['symbol']}")
            return plan
            
        except Exception as e:
            logger.error(f"Error generating execution plan: {e}")
            return None


# Test
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    executor = TradeExecutor("https://mainnet.helius-rpc.com/?api-key=test")
    
    print("\n" + "="*70)
    print("TRADE EXECUTOR TEST")
    print("="*70)
    
    # Test 1: Get prices
    print("\n[Test 1] Fetching token prices...")
    prices = executor.get_token_prices([
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
        executor.WRAPPED_SOL,
    ])
    for mint, price in prices.items():
        print(f"  {mint[:8]}...: ${price:.8f}")
    
    # Test 2: Get swap quote
    print("\n[Test 2] Getting swap quote...")
    quote = executor.get_swap_quote(
        executor.WRAPPED_SOL,
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        int(0.1 * 1e9),  # 0.1 SOL
    )
    if quote:
        print(f"  Output: {quote['output_amount']} tokens")
        print(f"  Impact: {quote['price_impact_pct']:.2f}%")
    
    # Test 3: Validate swap safety
    print("\n[Test 3] Validating swap safety...")
    if quote:
        is_safe, msg = executor.validate_swap_safety(quote)
        print(f"  Safe: {is_safe} - {msg}")
    
    # Test 4: Estimate buy amount
    print("\n[Test 4] Buy sizing...")
    amount, risk = executor.estimate_buy_amount(1.0, risk_percentage=5.0)
    print(f"  Amount: {amount/1e9:.4f} SOL (${risk:.2f})")
    
    print("\n" + "="*70)
    print("[OK] TRADE EXECUTOR TEST COMPLETE")
    print("="*70)
