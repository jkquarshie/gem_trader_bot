"""
Integration test: Find tokens -> Check for rugs -> Analyze charts
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
from src.rug_checker import RugChecker
from src.scanner import TokenScanner
from src.chart_analyzer import ChartAnalyzer
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
    
    print("\n" + "="*70)
    print("GEM TRADER BOT - FULL INTEGRATION TEST")
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
        print(f"   24h Volume: ${token['volume_24h_usd']:,.0f}")
    
    # Stage 2: Rug check top token
    print("\n" + "="*70)
    print("[Stage 2] Checking for rugs...")
    print("-" * 70)
    
    test_token = trending[0]['mint']
    print(f"\nRug checking {trending[0]['symbol']}...")
    
    rug_result = rug_checker.check_token(test_token)
    
    print(f"\nRug Check Results:")
    print(f"  Risk Score: {rug_result['risk_score']}/100")
    print(f"  Recommendation: {rug_result['recommendation']}")
    print(f"  Is Scam: {rug_result['is_likely_scam']}")
    
    # Stage 3: Chart analysis
    print("\n" + "="*70)
    print("[Stage 3] Analyzing chart...")
    print("-" * 70)
    
    print(f"\nAnalyzing chart for {trending[0]['symbol']}...")
    
    chart_result = chart_analyzer.analyze_token_chart(test_token)
    
    print(f"\nChart Analysis Results:")
    print(f"  RSI(14): {chart_result['rsi']:.2f}")
    print(f"  Support: ${chart_result['support']:.8f}")
    print(f"  Resistance: ${chart_result['resistance']:.8f}")
    print(f"  Volume Trend: {chart_result['volume_trend']}")
    print(f"  Signal: {chart_result['signal']}")
    print(f"  Confidence: {chart_result['score']}/100")
    
    # Overall recommendation
    print("\n" + "="*70)
    print("OVERALL RECOMMENDATION")
    print("="*70)
    
    rug_safe = rug_result['risk_score'] < 60
    chart_bullish = chart_result['signal'] in ['BUY', 'STRONG_BUY']
    
    if rug_safe and chart_bullish:
        recommendation = "BUY"
        color = "green"
    elif rug_safe and chart_result['signal'] == 'HOLD':
        recommendation = "HOLD"
        color = "yellow"
    else:
        recommendation = "SKIP"
        color = "red"
    
    print(f"\nToken: {trending[0]['symbol']}")
    print(f"Recommendation: {recommendation}")
    print(f"  - Rug Risk: {rug_result['risk_score']}/100 ({'Safe' if rug_safe else 'Risky'})")
    print(f"  - Chart Signal: {chart_result['signal']} (Confidence: {chart_result['score']}/100)")
    print(f"  - Overall: {recommendation}")
    
    print("\n" + "="*70)
    print("[OK] INTEGRATION TEST COMPLETE")
    print("="*70)
    
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
