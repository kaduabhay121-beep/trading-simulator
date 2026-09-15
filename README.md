# TradeLab Paper Bot v2.10.5 — Historical Chart Trading Manual Risk Fix

Based on v2.10.4. This release fixes historical manual chart trading so BUY/SELL orders do not inherit the live bot's 0.6%/1.2% SL/Target automatically.

## Historical chart trading
- Manual BUY/SELL trades default to **no automatic SL/Target**.
- Optional **SL/Target** checkbox enables manual price-based SL and/or Target.
- Entry candle is never checked for SL/Target immediately after placing a market trade; checks begin on the next revealed candle.
- Unrealized P&L continues updating as candles advance.
- BUY opens LONG; SELL opens SHORT; opposite order closes the current position.
- Manual/EOD/SL/TARGET exit reasons remain stored permanently.
- Paper/simulated execution only. No Angel One order placement is used.

## Deploy
Replace the current app files with `app.py`, `index.html`, and `requirements.txt`. Keep existing Render environment variables, including `DATABASE_URL` for Neon.
