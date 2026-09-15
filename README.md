# TradeLab v2.10.6 — Historical Chart Trading P&L / Quantity / Saved Data Fix

## Fixes
- Historical manual trades remain paper-only.
- Historical manual SL/Target remain opt-in and do not inherit live bot risk settings.
- Historical P&L is recalculated from the currently revealed candle price and position quantity on every candle advance.
- Historical position is now synchronized with the main chart P&L overlay, including LONG/SHORT, quantity, entry, current P&L, SL and Target.
- Overlay CLOSE works for historical positions.
- Historical quantity now supports direct entry up to 100,000 units plus −/+ stepper buttons.
- Quantity is preserved for the active historical position and is used in unrealized/realized P&L.
- Added SAVED button in the historical controller to jump to Research History.
- Historical research/trade records remain permanent when DATABASE_URL points to Neon PostgreSQL.
- No Angel One order-placement API is used.
