# Trading Simulator — Paper Bot v2.4 Research Upgrade

- Angel One is used for market data only. No real order-placement API is used.
- Paper capital: ₹10,00,000.
- Live paper wallet P&L is reflected in balance/equity.
- Strategies: EMA 9/15, RSI Mean Reversion, VWAP Reversion, Breakout, RBS (Resistance Becomes Support), SBR (Support Becomes Resistance).
- Research timeframes: 1m, 3m, 5m, 10m, 15m, 30m, 1h.
- Research supports multiple lot multipliers and Intraday/BTST modes.
- Intraday replay does not carry positions overnight; BTST exits at the next session open.
- Risk/reward ratio is stored with each test/trade.
- Research runs and trade details are stored in SQLite for at least 30 days with automatic retention cleanup.
- CAS is treated as a closing-session market-context feature, not a strategy.
- Settled/indicative pre-open information is treated as opening-price context rather than a separate paper account.
- GIFT Nifty and NIFTY futures are treated as external market context; the app does not fabricate historical values when an external historical feed is unavailable.


## Research history storage
- Research runs and every stored trade are retained for 30 days in SQLite.
- The UI has Research history filters, per-run trade logs, and CSV exports for runs and all trades.
- For Render, configure a Persistent Disk and set `SIM_DB_PATH=/data/simulator_state.db` so the 30-day journal survives deploys/restarts. Without persistent storage, SQLite data can be lost when the instance filesystem is replaced.
- Local/Termux can keep the default `simulator_state.db` beside `app.py`.
