# Trading Simulator — Full Paper Bot

## Included
- Angel One SmartAPI market data via WebSocket (no real order placement)
- NIFTY and SENSEX live data and options
- Paper-only automated bot with EMA Cross, RSI Mean Reversion, VWAP Reversion and Breakout strategies
- Long-only bot execution with automatic stop-loss/target
- Daily loss lock, risk-per-trade sizing, max trades/day, max open positions and cooldown
- Automatic end-of-day paper exit
- SQLite persistence for account, positions, orders, journal and bot configuration
- Signal test without execution
- Backtest and strategy comparison dashboard
- Minimal mobile-first Bot UI

## Deploy
Upload `app.py`, `index.html`, `requirements.txt`, and `README.md` to the repository. Keep existing Angel One environment variables private.

Render: `pip install -r requirements.txt` then `python app.py`.

## Safety
The bot is paper-only. It never calls Angel One order-placement endpoints. `START` only enables simulated execution against live market-data prices. The bot remains stopped after a server restart.

## Validation
Backtest results are research results, not proof of profitability. Strategy status becomes `TESTED` after comparison; it is not marked production-validated.

## Persistence note
SQLite persists across browser refreshes and normal process continuity. Render ephemeral storage can be lost on service replacement/redeploy; use a persistent disk or PostgreSQL if durable cloud history across redeploys is required.
