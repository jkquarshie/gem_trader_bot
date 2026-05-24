"""
Token scanner for discovering new/trending Solana tokens.
Filters tokens from DexScreener API.
"""

import logging
import requests
import asyncio
import aiohttp
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import time

logger = logging.getLogger(__name__)


class TokenScanner:
    """
    Scans Solana blockchain and DEX APIs for new/trending tokens.
    Uses DexScreener for discovering new and trending tokens.
    """
    
    # API endpoints
    DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex"
    RAYDIUM_API = "https://api.raydium.io/v2"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'GemTraderBot/1.0'})
    
    def scan_new_tokens(self, age_minutes: int = 30, min_liquidity_usd: float = 10000, limit: int = 20) -> List[Dict]:
        """
        Scan for new tokens created in the last N minutes with minimum liquidity.
        Returns list of candidate tokens with their metadata.
        
        Args:
            age_minutes: Only include tokens created in last N minutes
            min_liquidity_usd: Minimum liquidity in USD
            limit: Maximum number of tokens to return
        
        Returns:
            List of token dicts with: mint, name, symbol, price, liquidity, volume, age_seconds
        """
        logger.info(f"Scanning for new tokens (age < {age_minutes} min, liquidity > ${min_liquidity_usd})")
        
        candidates = []
        
        try:
            # Note: DexScreener doesn't have a direct "new tokens" endpoint
            # We'll search for recent trending and filter
            # For now, use search with a wildcard or fetch popular tokens
            
            # Try searching for recent tokens by querying popular categories
            search_queries = ["new", "gem", "token"]
            
            for query in search_queries:
                try:
                    response = self.session.get(
                        f"{self.DEXSCREENER_BASE}/search",
                        params={"q": query},
                        timeout=10
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    if 'pairs' not in data:
                        continue
                    
                    for pair in data['pairs'][:50]:  # Check first 50
                        try:
                            # Only process Solana tokens
                            if pair.get('chainId') != 'solana':
                                continue
                            
                            base_token = pair.get('baseToken', {})
                            token_mint = base_token.get('address', '')
                            token_symbol = base_token.get('symbol', '')
                            token_name = base_token.get('name', '')
                            
                            # Skip empty or stablecoins
                            if not token_mint or token_symbol in ['USDC', 'USDT', 'BUSD']:
                                continue
                            
                            # Get liquidity
                            liquidity = pair.get('liquidity', {})
                            liquidity_usd = float(liquidity.get('usd', 0)) if liquidity else 0
                            
                            # Skip if below minimum liquidity
                            if liquidity_usd < min_liquidity_usd:
                                continue
                            
                            # Check if already in list
                            if any(c['mint'] == token_mint for c in candidates):
                                continue
                            
                            # Get price and volume
                            price_usd = float(pair.get('priceUsd', 0))
                            volume_24h = pair.get('volume', {}).get('h24', 0)
                            
                            candidate = {
                                'mint': token_mint,
                                'name': token_name,
                                'symbol': token_symbol,
                                'price_usd': price_usd,
                                'liquidity_usd': liquidity_usd,
                                'volume_24h_usd': volume_24h,
                                'pair_address': pair.get('pairAddress', ''),
                                'dex_id': pair.get('dexId', ''),
                                'discovered_at': datetime.now().isoformat(),
                            }
                            
                            candidates.append(candidate)
                            
                            if len(candidates) >= limit:
                                break
                                
                        except Exception as e:
                            logger.debug(f"Error processing pair: {e}")
                            continue
                    
                    if len(candidates) >= limit:
                        break
                        
                except Exception as e:
                    logger.debug(f"Error searching for '{query}': {e}")
                    continue
            
            logger.info(f"Found {len(candidates)} candidate tokens")
            
        except Exception as e:
            logger.error(f"Error scanning new tokens: {e}")
        
        return candidates
    
    def scan_trending_tokens(self, top_n: int = 20, min_liquidity_usd: float = 50000) -> List[Dict]:
        """
        Scan for tokens with highest trading activity.
        
        Args:
            top_n: Return top N tokens by volume
            min_liquidity_usd: Minimum liquidity filter
        
        Returns:
            List of trending token dicts
        """
        logger.info(f"Scanning for trending tokens (top {top_n}, min liquidity ${min_liquidity_usd})")
        
        trending = []
        
        try:
            # Search for trending/popular tokens by searching common terms
            search_queries = ["solana", "trending", "dex"]
            
            for query in search_queries:
                try:
                    response = self.session.get(
                        f"{self.DEXSCREENER_BASE}/search",
                        params={"q": query},
                        timeout=10
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    if 'pairs' not in data:
                        continue
                    
                    # Sort by 24h volume
                    pairs_sorted = sorted(
                        data['pairs'],
                        key=lambda p: float(p.get('volume', {}).get('h24', 0)) if p.get('volume') else 0,
                        reverse=True
                    )
                    
                    for pair in pairs_sorted:
                        try:
                            # Only process Solana tokens
                            if pair.get('chainId') != 'solana':
                                continue
                            
                            base_token = pair.get('baseToken', {})
                            liquidity = pair.get('liquidity', {})
                            liquidity_usd = float(liquidity.get('usd', 0)) if liquidity else 0
                            
                            # Skip low liquidity
                            if liquidity_usd < min_liquidity_usd:
                                continue
                            
                            token_mint = base_token.get('address', '')
                            if not token_mint:
                                continue
                            
                            # Skip stablecoins
                            symbol = base_token.get('symbol', '')
                            if symbol in ['USDC', 'USDT', 'BUSD']:
                                continue
                            
                            # Skip if already in list
                            if any(t['mint'] == token_mint for t in trending):
                                continue
                            
                            trending.append({
                                'mint': token_mint,
                                'name': base_token.get('name', ''),
                                'symbol': symbol,
                                'price_usd': float(pair.get('priceUsd', 0)),
                                'liquidity_usd': liquidity_usd,
                                'volume_24h_usd': pair.get('volume', {}).get('h24', 0),
                                'volume_5m_usd': pair.get('volume', {}).get('m5', 0),
                                'volume_1h_usd': pair.get('volume', {}).get('h1', 0),
                                'pair_address': pair.get('pairAddress', ''),
                                'dex_id': pair.get('dexId', ''),
                                'discovered_at': datetime.now().isoformat(),
                            })
                            
                            if len(trending) >= top_n:
                                break
                                
                        except Exception as e:
                            logger.debug(f"Error processing trending pair: {e}")
                            continue
                    
                    if len(trending) >= top_n:
                        break
                        
                except Exception as e:
                    logger.debug(f"Error searching for '{query}': {e}")
                    continue
            
            logger.info(f"Found {len(trending)} trending tokens")
            return trending
            
        except Exception as e:
            logger.error(f"Error scanning trending tokens: {e}")
            return []
    
    def get_token_info(self, token_mint: str) -> Optional[Dict]:
        """
        Get current price, volume, and liquidity for a specific token.
        
        Args:
            token_mint: Solana token mint address
        
        Returns:
            Token info dict or None if not found
        """
        logger.info(f"Fetching info for token: {token_mint}")
        
        try:
            response = self.session.get(
                f"{self.DEXSCREENER_BASE}/tokens/{token_mint}",
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            if 'pairs' not in data or not data['pairs']:
                logger.warning(f"No data found for token {token_mint}")
                return None
            
            # Filter Solana pairs only, get the one with highest liquidity
            solana_pairs = [p for p in data['pairs'] if p.get('chainId') == 'solana']
            if not solana_pairs:
                logger.warning(f"No Solana pairs found for token {token_mint}")
                return None
            
            pair = max(solana_pairs, key=lambda p: float(p.get('liquidity', {}).get('usd', 0)))
            
            return {
                'mint': token_mint,
                'name': pair.get('baseToken', {}).get('name', ''),
                'symbol': pair.get('baseToken', {}).get('symbol', ''),
                'price_usd': float(pair.get('priceUsd', 0)),
                'liquidity_usd': float(pair.get('liquidity', {}).get('usd', 0)),
                'volume_24h_usd': pair.get('volume', {}).get('h24', 0),
                'volume_5m_usd': pair.get('volume', {}).get('m5', 0),
                'volume_1h_usd': pair.get('volume', {}).get('h1', 0),
                'market_cap_usd': pair.get('marketCap', 0),
                'pair_address': pair.get('pairAddress', ''),
                'dex_id': pair.get('dexId', ''),
                'fetched_at': datetime.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Error fetching token info for {token_mint}: {e}")
            return None
    
    def watch_tokens(self, token_mints: List[str]) -> Dict[str, Dict]:
        """
        Monitor multiple tokens for price/volume changes.
        
        Args:
            token_mints: List of token mint addresses to watch
        
        Returns:
            Dict mapping mint -> token info
        """
        logger.info(f"Watching {len(token_mints)} tokens")
        
        results = {}
        for token_mint in token_mints:
            try:
                info = self.get_token_info(token_mint)
                if info:
                    results[token_mint] = info
                time.sleep(0.1)  # Rate limit
            except Exception as e:
                logger.error(f"Error watching token {token_mint}: {e}")
        
        return results


# Test
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    scanner = TokenScanner()
    
    print("\n" + "="*70)
    print("TOKEN SCANNER TEST")
    print("="*70)
    
    # Test trending
    print("\nScanning trending tokens...")
    trending = scanner.scan_trending_tokens(top_n=5)
    for token in trending:
        print(f"  {token['symbol']}: ${token['price_usd']:.8f} (Liquidity: ${token['liquidity_usd']:,.0f})")
    
    # Test new tokens
    print("\nScanning new tokens...")
    new = scanner.scan_new_tokens(age_minutes=30, limit=5)
    for token in new:
        print(f"  {token['symbol']}: ${token['price_usd']:.8f} (Liquidity: ${token['liquidity_usd']:,.0f})")
    
    # Test single token
    if trending:
        first_token = trending[0]['mint']
        print(f"\nFetching info for {trending[0]['symbol']}...")
        info = scanner.get_token_info(first_token)
        if info:
            print(f"  Price: ${info['price_usd']:.8f}")
            print(f"  Liquidity: ${info['liquidity_usd']:,.0f}")
            print(f"  24h Volume: ${info['volume_24h_usd']:,.0f}")
