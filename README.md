# TradeLab v2.10 — Historical Chart Trading

v2.10 adds interactive, candle-by-candle historical chart trading to the existing TradeLab paper-trading simulator.

## What is new
- Load an exact previous NSE/BSE trading day from Angel One historical candles.
- Historical chart mode for NIFTY and SENSEX.
- 1m / 3m / 5m / 10m / 15m / 30m / 1h replay timeframes.
- Future candles remain hidden until the user advances the replay.
- PREV / PLAY / PAUSE / NEXT controls with 1x / 2x / 5x / 10x speed.
- Historical BUY opens a simulated long position at the revealed candle close.
- Historical SELL closes the simulated long position.
- Long-press limit-order workflow can be used in historical mode; the order fills only if the revealed candle touches the requested price.
- Stop-loss and target are checked against each newly revealed candle.
- Historical P&L and session capital are separate from the live paper account.
- Every historical chart trade is permanently stored in the research database.
- Historical replay sessions and exact-day candle datasets remain permanently retained.
- Existing live Angel One WebSocket chart, paper trading, bot, research matrix, replay, and Neon persistence are preserved.

## Storage
- `DATABASE_URL` -> PostgreSQL/Neon on Render.
- `SIM_DB_PATH` -> local SQLite fallback.
- No automatic 30-day deletion.

## Safety
Angel One is used only for market/historical data. This build does **not** call Angel One order-placement APIs. Historical chart BUY/SELL actions are simulator-only and are stored as paper trades.
