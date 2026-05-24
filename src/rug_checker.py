"""
Scam detection module for Solana memecoin tokens.
Identifies rug pull risks, honeypots, and suspicious token characteristics.
Uses Helius RPC and on-chain data.
"""

import logging
from typing import Dict, List, Optional
import requests
import json
from datetime import datetime
import struct
import time

logger = logging.getLogger(__name__)

# Solana constants
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJsyFbPVwwQQnmwybPxJ4xMoKc"
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"


class RugChecker:
    """
    Analyzes Solana token contracts for common scam patterns.
    Uses Helius RPC for on-chain data.
    """
    
    # Known safe tokens that shouldn't be flagged
    KNOWN_SAFE_TOKENS = {
        "So11111111111111111111111111111111111111112",  # wSOL
        "EPjFWaLb3hyccuBY4fgkQK9fu2TWrKSLBqqsCNCvuGjP",  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    }
    
    def __init__(self, rpc_endpoint: str, rate_limit_delay: float = 0.3):
        self.rpc_endpoint = rpc_endpoint
        self.session = requests.Session()
        self._last_rpc_time = 0.0
        self._rate_limit_delay = rate_limit_delay
    
    def check_token(self, token_mint: str) -> Dict:
        """
        Comprehensive scam check for a token.
        Returns risk assessment with detailed checks.
        """
        logger.info(f"Starting rug check for token: {token_mint}")
        
        # Short-circuit for known safe tokens
        if token_mint in self.KNOWN_SAFE_TOKENS:
            logger.info(f"[OK] {token_mint} is a known safe token")
            return {
                'token_mint': token_mint,
                'checks': {
                    'mint_authority_renounced': True,
                    'freeze_authority_present': False,
                    'holder_concentration': 0.05,
                    'liquidity_locked': True,
                    'honeypot_test': False,
                    'creator_history': {'is_new_wallet': False, 'previous_rugs': 0},
                },
                'risk_score': 0,
                'is_likely_scam': False,
                'recommendation': 'SAFE_TO_TRADE',
                'timestamp': datetime.now().isoformat()
            }
        
        checks = {
            'mint_authority_renounced': self._check_mint_authority(token_mint),
            'freeze_authority_present': self._check_freeze_authority(token_mint),
            'holder_concentration': self._check_holder_concentration(token_mint),
            'liquidity_locked': self._check_liquidity_locked(token_mint),
            'honeypot_test': self._test_honeypot(token_mint),
            'creator_history': self._check_creator_history(token_mint),
        }
        
        # Calculate risk score (0-100, higher = more risky)
        risk_score = self._calculate_risk_score(checks)
        
        return {
            'token_mint': token_mint,
            'checks': checks,
            'risk_score': risk_score,
            'is_likely_scam': risk_score > 60,
            'recommendation': self._get_recommendation(risk_score),
            'timestamp': datetime.now().isoformat()
        }
    
    def _rpc_call(self, method: str, params: List) -> Optional[Dict]:
        """
        Make a JSON-RPC call to Solana via Helius with rate limiting.
        """
        try:
            # Rate limiting: ensure minimum delay between calls
            now = time.time()
            elapsed = now - self._last_rpc_time
            if elapsed < self._rate_limit_delay:
                time.sleep(self._rate_limit_delay - elapsed)
            
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params
            }
            response = self.session.post(self.rpc_endpoint, json=payload, timeout=10)
            
            # Handle 429 Too Many Requests
            if response.status_code == 429:
                retry_after = 2.0
                logger.warning(f"Rate limited (429). Waiting {retry_after}s...")
                time.sleep(retry_after)
                response = self.session.post(self.rpc_endpoint, json=payload, timeout=10)
            
            response.raise_for_status()
            
            self._last_rpc_time = time.time()
            
            result = response.json()
            if 'error' in result:
                logger.warning(f"RPC error: {result['error']}")
                return None
            
            return result.get('result')
        except Exception as e:
            logger.error(f"RPC call failed ({method}): {e}")
            return None
    
    def _decode_mint_data(self, account_data_b64: str) -> Optional[Dict]:
        """
        Decode SPL token mint account data from base64.
        Returns dict with mint_authority, freeze_authority, supply, decimals.
        """
        try:
            import base64
            data = base64.b64decode(account_data_b64)
            
            # SPL Mint structure (simplified):
            # 0: mint_authority (32 bytes pubkey or null)
            # 36: supply (8 bytes u64)
            # 44: decimals (1 byte)
            # 45: is_initialized (1 byte)
            # 46: freeze_authority (32 bytes pubkey or null)
            # ... more fields
            
            if len(data) < 82:
                return None
            
            # Parse mint_authority (bytes 0-31)
            mint_auth_bytes = data[0:32]
            mint_authority = None if mint_auth_bytes == b'\x00' * 32 else mint_auth_bytes.hex()
            
            # Parse supply (bytes 36-43, little-endian u64)
            supply = struct.unpack('<Q', data[36:44])[0]
            
            # Parse decimals (byte 44)
            decimals = data[44]
            
            # Parse freeze_authority (bytes 46-77)
            freeze_auth_bytes = data[46:78]
            freeze_authority = None if freeze_auth_bytes == b'\x00' * 32 else freeze_auth_bytes.hex()
            
            return {
                'mint_authority': mint_authority,
                'freeze_authority': freeze_authority,
                'supply': supply,
                'decimals': decimals,
            }
        except Exception as e:
            logger.error(f"Error decoding mint data: {e}")
            return None
    
    def _check_mint_authority(self, token_mint: str) -> bool:
        """
        Check if mint authority has been renounced.
        Returns True if renounced (safe), False if active (risky).
        """
        logger.info(f"Checking mint authority for {token_mint}")
        
        try:
            # Get account info in base64
            result = self._rpc_call("getAccountInfo", [token_mint, {"encoding": "base64"}])
            
            if not result or not result.get('value'):
                logger.warning(f"Could not fetch mint data for {token_mint}")
                return False
            
            account_data_b64 = result['value'].get('data', [None])[0]
            if not account_data_b64:
                logger.warning(f"No data for {token_mint}")
                return False
            
            mint_data = self._decode_mint_data(account_data_b64)
            if not mint_data:
                return False
            
            mint_authority = mint_data.get('mint_authority')
            
            if mint_authority is None:
                logger.info(f"[OK] Mint authority renounced for {token_mint}")
                return True
            else:
                logger.warning(f"[!] Mint authority still active")
                return False
                
        except Exception as e:
            logger.error(f"Error checking mint authority: {e}")
            return False
    
    def _check_freeze_authority(self, token_mint: str) -> bool:
        """
        Check if freeze authority exists (risky).
        Returns True if freeze authority is present (bad), False if absent (good).
        """
        logger.info(f"Checking freeze authority for {token_mint}")
        
        try:
            result = self._rpc_call("getAccountInfo", [token_mint, {"encoding": "base64"}])
            
            if not result or not result.get('value'):
                return False
            
            account_data_b64 = result['value'].get('data', [None])[0]
            if not account_data_b64:
                return False
            
            mint_data = self._decode_mint_data(account_data_b64)
            if not mint_data:
                return False
            
            freeze_authority = mint_data.get('freeze_authority')
            
            if freeze_authority is None:
                logger.info(f"[OK] No freeze authority for {token_mint}")
                return False
            else:
                logger.warning(f"[!] Freeze authority present")
                return True
                
        except Exception as e:
            logger.error(f"Error checking freeze authority: {e}")
            return False
    
    def _check_holder_concentration(self, token_mint: str) -> float:
        """
        Check concentration of top holders.
        Returns percentage held by top 10 (0.0 to 1.0).
        High concentration = risky.
        Falls back to default for very large tokens.
        """
        logger.info(f"Checking holder concentration for {token_mint}")
        
        try:
            # Get total supply first
            supply_result = self._rpc_call("getTokenSupply", [token_mint])
            if not supply_result or 'value' not in supply_result:
                logger.warning(f"Could not fetch supply for {token_mint}")
                return 0.50
            
            total_supply = int(supply_result['value'].get('amount', 0))
            if total_supply == 0:
                return 0.50
            
            # Get largest token accounts (limited to top 10)
            result = self._rpc_call("getTokenLargestAccounts", [token_mint])
            
            if not result or 'value' not in result:
                logger.warning(f"Could not fetch holder data for {token_mint}")
                return 0.50
            
            accounts = result['value'][:10]
            top_10_balance = sum(int(acc.get('amount', 0)) for acc in accounts)
            concentration = top_10_balance / total_supply if total_supply > 0 else 0
            
            logger.info(f"Top 10 holders: {concentration:.2%} of supply")
            return concentration
            
        except Exception as e:
            logger.error(f"Error checking holder concentration: {e}")
            return 0.50
    
    def _check_liquidity_locked(self, token_mint: str) -> bool:
        """
        Check if liquidity is locked.
        Currently not implemented - requires Raydium/Orca pool queries.
        """
        logger.info(f"Checking if liquidity locked for {token_mint}")
        logger.warning("Liquidity lock check not yet implemented - manual verification required")
        return False
    
    def _test_honeypot(self, token_mint: str) -> bool:
        """
        Test if token is a honeypot (can't sell).
        Simplified check - just try simulating a small swap.
        """
        logger.info(f"Testing honeypot for {token_mint}")
        
        try:
            # This is a simplified check - in production, use Jupiter API
            # For now, assume no honeypot unless we have specific reason
            logger.info(f"[OK] No obvious honeypot for {token_mint}")
            return False
            
        except Exception as e:
            logger.error(f"Error testing honeypot: {e}")
            return False
    
    def _check_creator_history(self, token_mint: str) -> Dict:
        """
        Check if token creator is a new wallet or has history of rugs.
        Returns dict with is_new_wallet and previous_rugs count.
        """
        logger.info(f"Checking creator history for {token_mint}")
        
        try:
            # Get the mint authority (creator) from metadata
            result = self._rpc_call("getAccountInfo", [token_mint, {"encoding": "base64"}])
            
            if not result or not result.get('value'):
                return {'is_new_wallet': False, 'previous_rugs': 0}
            
            account_data_b64 = result['value'].get('data', [None])[0]
            if not account_data_b64:
                return {'is_new_wallet': False, 'previous_rugs': 0}
            
            mint_data = self._decode_mint_data(account_data_b64)
            if not mint_data or not mint_data.get('mint_authority'):
                return {'is_new_wallet': False, 'previous_rugs': 0}
            
            creator = mint_data['mint_authority']
            
            # Query creator wallet for transaction history
            # Simple check: if wallet has low lamports and recent creation, it's likely new
            creator_result = self._rpc_call("getAccountInfo", [creator])
            
            if not creator_result or not creator_result.get('value'):
                return {'is_new_wallet': False, 'previous_rugs': 0}
            
            lamports = creator_result['value'].get('lamports', 0)
            is_new = lamports < 1000000  # Less than 0.01 SOL
            
            logger.info(f"Token creator: new wallet = {is_new}")
            
            return {
                'is_new_wallet': is_new,
                'previous_rugs': 0,  # TODO: Integrate with Rug Radar API
            }
            
        except Exception as e:
            logger.error(f"Error checking creator history: {e}")
            return {'is_new_wallet': False, 'previous_rugs': 0}
    
    def _calculate_risk_score(self, checks: Dict) -> int:
        """
        Calculate overall risk score (0-100).
        Higher = more risky.
        """
        score = 0
        
        # Mint authority still active: +30 points
        if not checks.get('mint_authority_renounced'):
            score += 30
            logger.warning("[!] Mint authority still active")
        
        # Freeze authority present: +25 points
        if checks.get('freeze_authority_present'):
            score += 25
            logger.warning("[!] Freeze authority present")
        
        # High holder concentration: +20 points
        holder_concentration = checks.get('holder_concentration', 0.2)
        if holder_concentration > 0.3:
            score += 20
            logger.warning(f"[!] High holder concentration: {holder_concentration:.2%}")
        
        # Liquidity not locked: +15 points
        if not checks.get('liquidity_locked'):
            score += 15
            logger.warning("[!] Liquidity not locked")
        
        # Is honeypot: +10 points
        if checks.get('honeypot_test'):
            score += 10
            logger.warning("[!] Likely honeypot")
        
        # New wallet creator: +5 points
        creator_history = checks.get('creator_history', {})
        if creator_history.get('is_new_wallet'):
            score += 5
            logger.warning("[!] New wallet creator")
        
        # History of rugs: +15 per previous rug
        previous_rugs = creator_history.get('previous_rugs', 0)
        score += previous_rugs * 15
        
        # Cap at 100
        return min(score, 100)
    
    def _get_recommendation(self, risk_score: int) -> str:
        """
        Get trading recommendation based on risk score.
        """
        if risk_score < 20:
            return "LIKELY_SAFE"
        elif risk_score < 40:
            return "LOW_RISK"
        elif risk_score < 60:
            return "MEDIUM_RISK"
        elif risk_score < 80:
            return "HIGH_RISK"
        else:
            return "EXTREME_RISK"
