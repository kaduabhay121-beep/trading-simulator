# TradeLab live signal-engine drop-in

This package adds a deterministic CALL/PUT/NO_TRADE engine. It never calls an order-placement API.

## Integration
1. Copy `signal_engine.py` to the repository root.
2. In `app.py`, import `EngineConfig, SignalEngine`.
3. Build `tf_data` from the existing `state.get_instrument_chart_data()` for `5m`, `15m`, `1h` and pass `chain` from `state.get_option_chain()`.
4. Expose the result at `/api/signal` and cache it for ~2 seconds. Do not hold `state.lock` while doing network I/O.
5. Feed broker `opnInterest` into `*_oi`. Only populate `*_chg_oi` from a genuine second broker sample (or a dedicated broker field); never fabricate it.
6. Keep existing paper risk controls authoritative.

The engine is intentionally conservative: missing data produces NO_TRADE.

## TradeLab goal

TradeLab is being built as a **supreme trading simulator and market-research laboratory** for NIFTY and SENSEX. The signal/rule engine is only one implementation component.

The product goal is to help a trader:

- practice and measure trading skill without risking real capital;
- understand the market through multi-timeframe structure, momentum, volume, VWAP, support/resistance, breakouts and option-chain context;
- test strategies and risk rules on historical sessions;
- replay sessions candle-by-candle/tick-by-tick without look-ahead;
- collect market data during market hours and run the same analysis completely offline after market close;
- identify and study high-quality setups before considering them for a real trading account.

**No simulator can guarantee profitable trades.** TradeLab should therefore optimize for reproducible evidence, realistic execution simulation, risk control, transparent reasoning and out-of-sample validation rather than promises of profit.

### Online → Offline Data Vault

During market hours, when Angel One data is enabled, TradeLab stores the received NIFTY/SENSEX 1-minute session data locally and periodically stores real option-chain snapshots. After market close, Offline Engine Test reads only this persisted data; it does not contact Angel One or place orders.

The local SQLite database is the zero-setup/free fallback. A hosted PostgreSQL database can be used when the deployment requires it, but historical replay remains designed around persisted local/session data.

### Product architecture

```
Market data
    ↓
Data Vault
    ↓
Market Overview + Rule Engine + Strategy Lab
    ↓
Paper Execution / Replay / Backtest
    ↓
Analytics + Trade Journal + Skill Measurement
    ↓
Evidence for real-account decisions
```

The long-term Android direction is a native Android application with a local-first replay/database layer, while the Python engine remains reusable for research and broker connectivity.


## Native Android direction

TradeLab now includes a native Android module under `android/`. It is local-first and explicitly separates LIVE MARKET / paper mode from OFFLINE LAB. The Android client is intended to share the deterministic Rule Engine/data contracts with the existing Python research core rather than introduce a second trading logic path.

No historical NIFTY/SENSEX dataset is required from the user. The Data Vault is designed to bootstrap itself from genuine market information captured during market hours. A session is only eligible for offline research when the required local dataset has been captured and validated; missing data must not be fabricated.

The permanent product definition remains the uploaded TradeLab goal: genuine NIFTY/SENSEX information → local Data Vault → one deterministic Rule Engine → paper execution → journal/analytics → replay/backtest/strategy comparison/walk-forward validation, with no future-data leakage and no real orders.
