# TradeLab v2.10 — Historical Chart Trading + Option Replay

Builds on v2.9.1 Neon permanent research storage.

## v2.10 fixes/enhancements
- Historical chart now loads a full 1-minute session first and locally aggregates to 1m/3m/5m/10m/15m/30m/1h. This prevents the one-big-candle/single-candle replay problem.
- Historical replay controls are on the Chart only: HIST, date, market, instrument, timeframe, LOAD, PREV, PLAY/PAUSE, NEXT, speed, BUY, SELL, SAVE.
- Historical chart supports NIFTY and SENSEX index charts.
- Historical chart supports NIFTY/SENSEX option charts (CE/PE), expiry and strike selection.
- Option contract candles are fetched from Angel One historical data using the current instrument master. Recent active contracts are supported; expired F&O contracts are not guaranteed because Angel One's current scrip master does not expose expired contracts.
- Historical chart trades remain paper-only. No Angel One order placement API is called.
- Historical chart sessions and trades remain permanently stored in the configured Neon database (or SQLite fallback locally).

## Deploy
Upload these files to the `trading-simulator` GitHub repository on `main`:
- app.py
- index.html
- requirements.txt
- README.md

Do not upload this build to the Android repository.
