# GEM TRADER BOT — DEVELOPMENT GUIDE

## Setup

1. **Clone repo & install dependencies:**
   ```bash
   cd gem_trader_bot
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Set up environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your actual API keys and wallet path
   ```

3. **Get API Keys:**
   - Helius: https://www.helius.dev (for Solana RPC)
   - Birdeye: https://www.birdeye.so (for token data)
   - DexScreener: https://dexscreener.com (free, no key needed)
   - Twitter API (optional): https://developer.twitter.com

## Project Stages

### ✅ Stage 1: Rug Checker (skeleton complete)
- [ ] Implement `_check_mint_authority()` — call Solana RPC to verify mint authority is renounced
- [ ] Implement `_check_freeze_authority()` — check for freeze authority (red flag)
- [ ] Implement `_check_holder_concentration()` — fetch holder data from Helius/Birdeye
- [ ] Implement `_test_honeypot()` — simulate sell transaction
- [ ] Implement `_check_creator_history()` — analyze creator wallet history
- [ ] Test on known rug tokens

### ⏳ Stage 2: Token Scanner
- [ ] Implement `_fetch_from_dexscreener()` — call DexScreener API for new tokens
- [ ] Implement `_fetch_trending_from_birdeye()` — get trending tokens
- [ ] Add filtering logic (min liquidity, age, volume)
- [ ] Test scanner with live data

### ⏳ Stage 3: Chart Analysis
- [ ] Price/volume data fetching
- [ ] RSI, MACD indicators
- [ ] Support/resistance detection
- [ ] Volume spike detection

### ⏳ Stage 4: Sentiment Analysis
- [ ] Twitter mention velocity
- [ ] Discord/Telegram growth
- [ ] Sentiment polarity scoring
- [ ] Combine into vibe score

### ⏳ Stage 5: Trade Execution
- [ ] Jupiter API integration
- [ ] Wallet keypair management
- [ ] Buy/sell transaction construction
- [ ] Position tracking

### ⏳ Stage 6: Bot Orchestration
- [ ] Main loop orchestration
- [ ] Trade approval notifications (email/Telegram)
- [ ] Trade history database
- [ ] P&L tracking

## Testing

```bash
# Test rug checker
python -m src.rug_checker

# Test scanner
python -m src.scanner

# Run main bot
python src/main.py
```

## Architecture Notes

- **Async/await** for concurrent API calls
- **No hard keys in code** — use .env file
- **Logging** to both file and console
- **Modular design** — each component can be tested independently

## Next Step

Start with **Stage 1: Rug Checker** — implement the actual Solana RPC calls to fetch token metadata.
