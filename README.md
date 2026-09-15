# TradeLab v2.10.7 — Historical Lots, P&L & Replay Integrity

This release builds on v2.10.6 and fixes historical chart trading semantics.

## Key changes
- Historical OPTION quantity is now **LOTS**, not raw units.
- Actual contract lot size is carried from the selected Angel One instrument master when available.
- Fallback lot sizes: NIFTY options 65; SENSEX options 20; index 1.
- P&L = price movement × actual quantity (lots × lot size), for both LONG and SHORT.
- Historical position overlay shows Lots, Qty, Entry, LTP and live unrealized P&L.
- Historical controller shows Lot size, calculated Qty, Available balance, Equity and total P&L.
- Saved historical trades store lots, lot size, quantity, entry/exit, P&L, brackets and exit reason.
- Historical Research History run lot count is updated to the session's selected lots.
- PREV now rewinds/reconstructs historical trading state instead of merely moving the chart cursor.
- SENSEX lot-size handling corrected to 20.
- Manual historical SL/Target remains opt-in and does not inherit bot SL/Target.
- Entry candle is not checked for automatic SL/Target after entry; checking starts on subsequent candles.
- Paper-only: no Angel One order-placement API is called.
- Permanent storage remains supported through DATABASE_URL/Neon with local SQLite fallback.
