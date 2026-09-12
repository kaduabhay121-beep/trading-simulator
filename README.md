# Trading Simulator — Full Paper Bot v2

## Included
- Angel One SmartAPI WebSocket market data only.
- Paper-only order simulation; no Angel One order-placement API is called.
- NIFTY and SENSEX.
- Index execution or option execution using ATM, ITM 1–3, or OTM 1–3 long-call selection.
- EMA, RSI mean reversion, VWAP mean reversion, and breakout strategies.
- Automatic stop-loss/target, daily loss lock, max trades/open positions and cooldown.
- Persistent paper account, positions, orders, journal and bot configuration using SQLite.
- Bot starts stopped after server restart for safety.
- Closed-market historical replay/backtest. Replay does not modify the live paper account.
- Option replay uses historical option candles for the selected relative strike when available.

## Deploy
Replace the project files with:
- `app.py`
- `index.html`
- `requirements.txt`
- `README.md`

Render:
- Build: `pip install -r requirements.txt`
- Start: `python app.py`

Keep existing Angel One environment variables private and unchanged.

## Closed-market testing
Open **BOT → Research → Closed-market replay**. Select the underlying, strategy, and `INDEX`, `ATM`, `ITM 1–3`, or `OTM 1–3`, then tap **REPLAY**.

Replay is research-only: it uses historical candles, reports simulated P&L/Win%/Profit Factor/Drawdown, and does not alter the live paper account.

## Safety
The bot is deliberately long-only for automated option entries. A SELL signal exits an existing bot position rather than opening a short position. Direct manual simulator orders remain separate.

Backtest/replay results are not proof of profitability. Slippage, brokerage, spread, latency and live execution differences are not fully modeled.
