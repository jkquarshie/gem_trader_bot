"""
Technical analysis module for chart pattern detection.
Calculates RSI, support/resistance levels, and volume analysis.
Uses pure Python (no pandas/numpy).
"""

import logging
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta
import requests

logger = logging.getLogger(__name__)


class ChartAnalyzer:
    """
    Analyzes token price charts for technical signals.
    Calculates RSI, support/resistance, volume trends.
    """
    
    # OHLCV data sources
    BIRDEYE_API = "https://api.birdeye.so/public"
    DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'GemTraderBot/1.0'})
    
    def get_ohlcv_data(self, token_mint: str, time_frame: str = "5m", limit: int = 100) -> List[Dict]:
        """
        Fetch OHLCV (Open, High, Low, Close, Volume) data for a token.
        
        Args:
            token_mint: Solana token mint address
            time_frame: "1m", "5m", "15m", "1h", "4h", "1d"
            limit: Number of candles to fetch
        
        Returns:
            List of OHLCV dicts with: timestamp, open, high, low, close, volume
        """
        logger.info(f"Fetching {time_frame} OHLCV data for {token_mint}")
        
        try:
            # Try DexScreener (provides basic data)
            token_info = self._get_token_info(token_mint)
            if not token_info:
                logger.warning(f"Could not fetch token info for {token_mint}")
                return []
            
            # Since DexScreener doesn't provide historical OHLCV directly,
            # we'll simulate it using current price data
            # In production, use Birdeye or Photon APIs
            
            # For now, return current snapshot as placeholder
            current_candle = {
                'timestamp': datetime.now().isoformat(),
                'open': token_info.get('price_usd', 0),
                'high': token_info.get('price_usd', 0),
                'low': token_info.get('price_usd', 0),
                'close': token_info.get('price_usd', 0),
                'volume': token_info.get('volume_24h_usd', 0),
            }
            
            logger.info(f"Fetched current price: ${current_candle['close']:.8f}")
            return [current_candle]
            
        except Exception as e:
            logger.error(f"Error fetching OHLCV data: {e}")
            return []
    
    def calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """
        Calculate Relative Strength Index (RSI).
        
        Args:
            prices: List of closing prices (oldest first)
            period: RSI period (usually 14)
        
        Returns:
            RSI value (0-100). >70 = overbought, <30 = oversold
        """
        if len(prices) < period + 1:
            logger.warning(f"Not enough data for RSI (need {period + 1}, got {len(prices)})")
            return 50.0  # Default to neutral
        
        try:
            # Calculate price changes
            deltas = []
            for i in range(1, len(prices)):
                deltas.append(prices[i] - prices[i - 1])
            
            # Separate gains and losses
            gains = [d if d > 0 else 0 for d in deltas]
            losses = [-d if d < 0 else 0 for d in deltas]
            
            # Calculate average gain/loss over period
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            
            # Calculate RSI
            if avg_loss == 0:
                rsi = 100.0 if avg_gain > 0 else 50.0
            else:
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
            
            logger.debug(f"RSI({period}): {rsi:.2f}")
            return rsi
            
        except Exception as e:
            logger.error(f"Error calculating RSI: {e}")
            return 50.0
    
    def find_support_resistance(self, prices: List[float], window: int = 20) -> Tuple[float, float]:
        """
        Find support and resistance levels using simple min/max in window.
        
        Args:
            prices: List of prices (oldest first)
            window: Lookback window in candles
        
        Returns:
            Tuple of (support_level, resistance_level)
        """
        if len(prices) < window:
            logger.warning(f"Not enough data for support/resistance (need {window}, got {len(prices)})")
            current = prices[-1] if prices else 0
            return (current * 0.95, current * 1.05)
        
        try:
            recent_prices = prices[-window:]
            support = min(recent_prices)
            resistance = max(recent_prices)
            
            logger.debug(f"Support: ${support:.8f}, Resistance: ${resistance:.8f}")
            return (support, resistance)
            
        except Exception as e:
            logger.error(f"Error finding support/resistance: {e}")
            return (0, 0)
    
    def calculate_moving_average(self, prices: List[float], period: int = 20) -> float:
        """
        Calculate simple moving average.
        
        Args:
            prices: List of prices (oldest first)
            period: MA period
        
        Returns:
            Current moving average
        """
        if len(prices) < period:
            # Use all available data
            period = len(prices)
        
        try:
            ma = sum(prices[-period:]) / period
            logger.debug(f"MA({period}): ${ma:.8f}")
            return ma
        except Exception as e:
            logger.error(f"Error calculating MA: {e}")
            return 0
    
    def analyze_volume_trend(self, volumes: List[float], period: int = 20) -> str:
        """
        Analyze volume trend (increasing, decreasing, or stable).
        
        Args:
            volumes: List of volumes (oldest first)
            period: Period for trend analysis
        
        Returns:
            Trend: "INCREASING", "DECREASING", or "STABLE"
        """
        if len(volumes) < period:
            logger.warning(f"Not enough volume data (need {period}, got {len(volumes)})")
            return "UNKNOWN"
        
        try:
            recent_vol = sum(volumes[-period:]) / period
            earlier_vol = sum(volumes[-period*2:-period]) / period if len(volumes) >= period * 2 else recent_vol
            
            if recent_vol > earlier_vol * 1.1:
                trend = "INCREASING"
            elif recent_vol < earlier_vol * 0.9:
                trend = "DECREASING"
            else:
                trend = "STABLE"
            
            logger.debug(f"Volume trend: {trend}")
            return trend
            
        except Exception as e:
            logger.error(f"Error analyzing volume: {e}")
            return "UNKNOWN"
    
    def detect_volume_spike(self, volume_5m: float, volume_1h: float) -> Dict:
        """
        Detect if volume is spiking compared to recent average.
        
        Args:
            volume_5m: Volume in last 5 minutes
            volume_1h: Volume in last 1 hour
        
        Returns:
            Dict with: is_spiking, spike_ratio, signal
        """
        if volume_5m <= 0 or volume_1h <= 0:
            return {'is_spiking': False, 'spike_ratio': 1.0, 'signal': 'NO_DATA'}
        
        # Extrapolate 5m volume to 1h (multiply by 12)
        extrapolated_1h = volume_5m * 12
        
        # Compare extrapolated to actual 1h volume
        if extrapolated_1h > 0 and volume_1h > 0:
            spike_ratio = extrapolated_1h / volume_1h
        else:
            spike_ratio = 1.0
        
        is_spiking = spike_ratio > 1.5  # 50%+ increase = spike
        
        if spike_ratio > 3.0:
            signal = "STRONG_SPIKE"
        elif spike_ratio > 1.5:
            signal = "SPIKE"
        else:
            signal = "NORMAL"
        
        logger.debug(f"Volume spike: ratio={spike_ratio:.1f}x, signal={signal}")
        
        return {
            'is_spiking': is_spiking,
            'spike_ratio': spike_ratio,
            'signal': signal,
        }
    
    def analyze_token_chart(self, token_mint: str, token_data: Dict = None) -> Dict:
        """
        Perform comprehensive chart analysis on a token.
        
        Args:
            token_mint: Solana token mint address
        
        Returns:
            Dict with: rsi, support, resistance, ma_50, volume_trend, signal
        """
        logger.info(f"Analyzing chart for {token_mint}")
        
        try:
            # Fetch OHLCV data
            ohlcv = self.get_ohlcv_data(token_mint, time_frame="5m", limit=100)
            
            if not ohlcv:
                logger.warning(f"No OHLCV data for {token_mint}")
                return {
                    'rsi': 50.0,
                    'support': 0,
                    'resistance': 0,
                    'ma_50': 0,
                    'volume_trend': 'UNKNOWN',
                    'volume_spike': 'NO_DATA',
                    'spike_ratio': 1.0,
                    'signal': 'INSUFFICIENT_DATA',
                    'score': 0,
                }
            
            # Extract prices and volumes
            prices = [candle['close'] for candle in ohlcv]
            volumes = [candle['volume'] for candle in ohlcv]
            
            # Calculate indicators
            rsi = self.calculate_rsi(prices, period=14)
            support, resistance = self.find_support_resistance(prices, window=20)
            ma_50 = self.calculate_moving_average(prices, period=50)
            volume_trend = self.analyze_volume_trend(volumes, period=10)
            
            # Volume spike detection from DexScreener data
            vol_spike = {}
            if token_data:
                vol_5m = float(token_data.get('volume_5m_usd', 0))
                vol_1h = float(token_data.get('volume_1h_usd', 0))
                vol_spike = self.detect_volume_spike(vol_5m, vol_1h)
            
            # Generate signal
            signal = self._generate_signal(rsi, prices[-1], support, resistance, ma_50, volume_trend, vol_spike.get('signal'))
            
            # Calculate confidence score (0-100)
            score = self._calculate_score(rsi, prices[-1], support, resistance, volume_trend, vol_spike.get('signal'))
            
            result = {
                'rsi': rsi,
                'support': support,
                'resistance': resistance,
                'current_price': prices[-1],
                'ma_50': ma_50,
                'volume_trend': volume_trend,
                'volume_spike': vol_spike.get('signal', 'NO_DATA'),
                'spike_ratio': vol_spike.get('spike_ratio', 1.0),
                'signal': signal,
                'score': score,
                'analyzed_at': datetime.now().isoformat(),
            }
            
            logger.debug(f"Chart analysis complete: {signal} (score: {score})")
            return result
            
        except Exception as e:
            logger.error(f"Error analyzing chart: {e}")
            return {
                'rsi': 50.0,
                'support': 0,
                'resistance': 0,
                'ma_50': 0,
                'volume_trend': 'UNKNOWN',
                'volume_spike': 'NO_DATA',
                'spike_ratio': 1.0,
                'signal': 'ERROR',
                'score': 0,
            }
    
    def _get_token_info(self, token_mint: str) -> Optional[Dict]:
        """Fetch current token info from DexScreener."""
        try:
            response = self.session.get(
                f"{self.DEXSCREENER_API}/tokens/{token_mint}",
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            if 'pairs' not in data or not data['pairs']:
                return None
            
            # Get Solana pair with highest liquidity
            solana_pairs = [p for p in data['pairs'] if p.get('chainId') == 'solana']
            if not solana_pairs:
                return None
            
            pair = max(solana_pairs, key=lambda p: float(p.get('liquidity', {}).get('usd', 0)))
            
            return {
                'price_usd': float(pair.get('priceUsd', 0)),
                'volume_24h_usd': pair.get('volume', {}).get('h24', 0),
                'liquidity_usd': float(pair.get('liquidity', {}).get('usd', 0)),
            }
        except Exception as e:
            logger.error(f"Error fetching token info: {e}")
            return None
    
    def _generate_signal(self, rsi: float, price: float, support: float, resistance: float, ma: float, vol_trend: str, vol_spike: str = None) -> str:
        """Generate buy/sell/hold signal based on indicators."""
        signals = []
        
        # RSI signals
        if rsi < 30:
            signals.append("OVERSOLD")
        elif rsi > 70:
            signals.append("OVERBOUGHT")
        
        # Price position signals
        if support > 0 and price < support * 1.05:
            signals.append("NEAR_SUPPORT")
        if resistance > 0 and price > resistance * 0.95:
            signals.append("NEAR_RESISTANCE")
        
        # MA signals
        if ma > 0 and price > ma * 1.02:
            signals.append("ABOVE_MA")
        elif ma > 0 and price < ma * 0.98:
            signals.append("BELOW_MA")
        
        # Volume signals
        if vol_trend == "INCREASING":
            signals.append("VOLUME_UP")
        elif vol_trend == "DECREASING":
            signals.append("VOLUME_DOWN")
        
        # Volume spike signal
        if vol_spike == "STRONG_SPIKE":
            signals.append("VOLUME_SPIKE_STRONG")
        elif vol_spike == "SPIKE":
            signals.append("VOLUME_SPIKE")
        
        # Aggregate signals
        if "OVERSOLD" in signals and "VOLUME_SPIKE" in signals:
            return "STRONG_BUY"
        elif "OVERSOLD" in signals and "VOLUME_UP" in signals:
            return "STRONG_BUY"
        elif "OVERBOUGHT" in signals and "VOLUME_SPIKE" in signals:
            return "STRONG_SELL"
        elif "OVERBOUGHT" in signals and "VOLUME_DOWN" in signals:
            return "STRONG_SELL"
        elif "OVERSOLD" in signals:
            return "BUY"
        elif "OVERBOUGHT" in signals:
            return "SELL"
        elif "NEAR_SUPPORT" in signals and "VOLUME_SPIKE" in signals:
            return "BUY"
        elif "NEAR_SUPPORT" in signals and "VOLUME_UP" in signals:
            return "BUY"
        elif "NEAR_RESISTANCE" in signals and "VOLUME_SPIKE" in signals:
            return "SELL"
        elif "NEAR_RESISTANCE" in signals and "VOLUME_DOWN" in signals:
            return "SELL"
        else:
            return "HOLD"
    
    def _calculate_score(self, rsi: float, price: float, support: float, resistance: float, vol_trend: str, vol_spike: str = None) -> int:
        """Calculate 0-100 confidence score for trade."""
        score = 50  # Start at neutral
        
        # RSI contribution
        if rsi < 30:
            score += 15  # Oversold = bullish
        elif rsi > 70:
            score -= 15  # Overbought = bearish
        
        # Support/Resistance contribution
        if support > 0 and price < support * 1.05:
            score += 10  # Near support = bullish
        if resistance > 0 and price > resistance * 0.95:
            score -= 10  # Near resistance = bearish
        
        # Volume contribution
        if vol_trend == "INCREASING":
            score += 10
        elif vol_trend == "DECREASING":
            score -= 5
        
        # Volume spike contribution
        if vol_spike == "STRONG_SPIKE":
            score += 20
        elif vol_spike == "SPIKE":
            score += 10
        
        return max(0, min(100, score))


# Test
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    analyzer = ChartAnalyzer()
    
    print("\n" + "="*70)
    print("CHART ANALYZER TEST")
    print("="*70)
    
    # Test with BONK
    token_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    print(f"\nAnalyzing {token_mint}...")
    
    result = analyzer.analyze_token_chart(token_mint)
    
    print(f"\nResults:")
    print(f"  RSI: {result['rsi']:.2f}")
    print(f"  Current Price: ${result['current_price']:.8f}")
    print(f"  Support: ${result['support']:.8f}")
    print(f"  Resistance: ${result['resistance']:.8f}")
    print(f"  MA(50): ${result['ma_50']:.8f}")
    print(f"  Volume Trend: {result['volume_trend']}")
    print(f"  Signal: {result['signal']}")
    print(f"  Confidence Score: {result['score']}/100")
