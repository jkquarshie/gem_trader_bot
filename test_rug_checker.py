"""
Quick test script for rug checker.
Tests with known safe tokens.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
from src.rug_checker import RugChecker
from src.logger import logger

# Load environment
load_dotenv()

def test_rug_checker():
    """Test rug checker with known tokens."""
    
    rpc_endpoint = os.getenv('RPC_ENDPOINT')
    if not rpc_endpoint:
        print("[!] ERROR: RPC_ENDPOINT not set in .env")
        return False
    
    logger.info(f"Using RPC: {rpc_endpoint}")
    
    # Initialize checker
    checker = RugChecker(rpc_endpoint)
    
    # Test tokens
    test_tokens = {
        'USDC': 'EPjFWaLb3hyccuBY4fgkQK9fu2TWrKSLBqqsCNCvuGjP',  # Should be safe (known)
        'USDT': 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB',  # Should be safe (known)
        'wSOL': 'So11111111111111111111111111111111111111112',      # Should be safe (known)
        'BONK': 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263',  # Test with real token
        'WIF': 'EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm',   # Test with real token
    }
    
    print("\n" + "="*70)
    print("GEM TRADER BOT - RUG CHECKER TEST")
    print("="*70)
    
    for name, mint in test_tokens.items():
        print(f"\nTesting {name} ({mint})")
        print("-" * 70)
        
        try:
            result = checker.check_token(mint)
            
            print(f"[OK] Risk Score: {result['risk_score']}/100")
            print(f"[OK] Recommendation: {result['recommendation']}")
            print(f"[OK] Is Likely Scam: {result['is_likely_scam']}")
            print("\n  Detailed Checks:")
            for check, value in result['checks'].items():
                if isinstance(value, dict):
                    print(f"    - {check}: {value}")
                elif isinstance(value, float):
                    print(f"    - {check}: {value:.2%}")
                else:
                    print(f"    - {check}: {value}")
            
        except Exception as e:
            print(f"[!] Error: {e}")
            return False
    
    print("\n" + "="*70)
    print("[OK] RUG CHECKER TEST COMPLETE")
    print("="*70)
    return True


if __name__ == "__main__":
    success = test_rug_checker()
    sys.exit(0 if success else 1)
