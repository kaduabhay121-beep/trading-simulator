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
