# TradeLab v2.10.9 — Historical Chart Stability Fix

Based on v2.10.8.

## Fixes
- Historical chart no longer resets vertical/horizontal pan on every revealed candle.
- Historical Y-axis is stabilized between candles to prevent repeated upward/downward drift caused by continuous auto-fitting.
- Y-range only recenters/expands when the visible data approaches the stable range boundary.
- User vertical pan is preserved during replay.
- Historical viewport is reset only when starting/exiting a historical session.
- Existing v2.10.8 P&L overlay CLOSE handling, lots/lot-size model, permanent history, and paper-only execution are retained.

Paper trading only. No Angel One order-placement API is used.
