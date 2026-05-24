"""
Integration test: Full trading pipeline with mock data
Rug check -> Chart analysis -> Execution plan
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
from src.rug_checker import RugChecker
from src.scanner import TokenScanner
from src.chart_analyzer import ChartAnalyzer
from src.trade_executor import TradeExecutor
from src.logger import logger

load_dotenv()

def main():
    rpc_endpoint = os.getenv('RPC_ENDPOINT')
    if not rpc_endpoint:
        print("[!] ERROR: RPC_ENDPOINT not set in .env")
        return False

    logger.info("Initializing bot modules...")

    rug_checker = RugChecker(rpc_endpoint)
    scanner = TokenScanner()
    chart_analyzer = ChartAnalyzer()
    executor = TradeExecutor(rpc_endpoint, wallet_address="YourWalletHere")

    print("\n" + "="*70)
    print("GEM TRADER BOT - FULL TRADE PIPELINE TEST")
    print("="*70)

    # Stage 1: Scan for tokens
    print("\n[Stage 1] Scanning for trending tokens...")
    print("-" * 70)

    trending = scanner.scan_trending_tokens(top_n=3, min_liquidity_usd=50000)

    if not trending:
        print("[!] No tokens found")
        return False

    for i, token in enumerate(trending, 1):
        print(f"\n{i}. {token['symbol']} ({token['mint'][:8]}...)")
        print(f"   Price: ${token['price_usd']:.8f}")
        print(f"   Liquidity: ${token['liquidity_usd']:,.0f}")

    # Use BONK for full pipeline demo (real token with known data)
    print("\n" + "="*70)
    print("[*] Using BONK for full pipeline demonstration")
    print("="*70)

    test_token = {
        'mint': 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263',
        'symbol': 'BONK',
        'name': 'Bonk',
        'price_usd': 0.00000602,
        'liquidity_usd': 10000000,
        'volume_24h_usd': 5000000,
    }

    # Stage 2: Rug check
    print("\n[Stage 2] Rug checking token...")
    print("-" * 70)

    rug_result = rug_checker.check_token(test_token['mint'])
    print(f"  Risk Score: {rug_result['risk_score']}/100 ({rug_result['recommendation']})")
    print(f"  Is Scam: {rug_result['is_likely_scam']}")

    # Stage 3: Chart analysis
    print("\n" + "="*70)
    print("[Stage 3] Analyzing chart...")
    print("-" * 70)

    chart_result = chart_analyzer.analyze_token_chart(test_token['mint'])
    print(f"  RSI: {chart_result['rsi']:.2f}")
    print(f"  Signal: {chart_result['signal']}")
    print(f"  Confidence: {chart_result['score']}/100")

    # Stage 4: Execution planning
    print("\n" + "="*70)
    print("[Stage 4] Generating execution plan...")
    print("-" * 70)

    wallet_balance = 1.0
    print(f"\nWallet balance: {wallet_balance} SOL")
    print(f"Risk tolerance: 5% per trade")

    execution_plan = executor.get_execution_plan(
        token_info=test_token,
        rug_result=rug_result,
        chart_result=chart_result,
        wallet_balance_sol=wallet_balance,
        risk_pct=5.0
    )

    if not execution_plan:
        print("[!] Failed to generate execution plan")
        return False

    print(f"\nExecution Plan:")
    print(f"  Decision: {execution_plan['decision']}")

    if execution_plan['decision'] == 'SKIP':
        print(f"  Reason: {execution_plan['reason']}")
        print(f"\n[OK] Bot would skip this token and continue scanning")
        return True

    # Stage 5: Display trade details
    print("\n" + "="*70)
    print("[Stage 5] TRADE READY FOR EXECUTION")
    print("="*70)

    print(f"\nToken: {execution_plan['token']}")
    print(f"Current Price: ${execution_plan['token_price']:.8f}")
    print(f"\nOrder Details:")
    print(f"  Buy Amount: {execution_plan['buy_amount_sol']:.4f} SOL")
    print(f"  Risk Amount: ${execution_plan['risk_usd']:.2f}")
    print(f"  Expected Output: {execution_plan['output_tokens']} tokens")
    print(f"  Price Impact: {execution_plan['price_impact_pct']:.2f}%")

    print(f"\nRisk Management:")
    print(f"  Entry Price: ${execution_plan['entry_price']:.8f}")
    print(f"  Take Profit (50%): ${execution_plan['take_profit_target']:.8f}")
    print(f"  Stop Loss (20%): ${execution_plan['stop_loss_level']:.8f}")

    print(f"\nDiligence Scores:")
    print(f"  Rug Score: {execution_plan['rug_score']}/100")
    print(f"  Chart Signal: {execution_plan['chart_signal']} ({execution_plan['chart_confidence']}/100)")

    print(f"\n[!] WAITING FOR USER APPROVAL VIA TELEGRAM BOT")
    print(f"    Bot will send alert and wait for user to confirm")
    print(f"    Once approved, will execute with Phantom wallet signature")

    print("\n" + "="*70)
    print("[OK] PIPELINE TEST COMPLETE - READY FOR STAGE 5 (TELEGRAM)")
    print("="*70)

    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
