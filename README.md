# TradeLab v2.11.0 — Historical Trading UI & Replay Engine

Based on the v2.10.9 historical chart build.

## Included fixes
- Stable historical Y-axis viewport: no repeated smooth vertical drift while replaying candles.
- Historical replay keeps manual viewport position; Y-range only expands when price approaches the edge.
- Manual historical lots are converted to actual quantity using the selected contract lot size.
- SENSEX option lot size is 20 units/lot when contract master data is unavailable.
- P&L uses actual quantity: (LTP-entry)*qty for LONG and (entry-LTP)*qty for SHORT.
- Historical P&L overlay shows lots, quantity, entry, LTP, price change and unrealized P&L.
- SL/Target/TSL display OFF when not configured.
- Historical controller distinguishes next-order lots from the currently open position.
- Historical overlay CLOSE uses a dedicated interaction layer and a busy guard.
- Historical CLOSE errors are surfaced in the status area instead of being silently swallowed.
- Historical close validates that the exit candle is not before the entry candle and persists the updated session.
- PREV uses the server replay-rewind state reconstruction.
- Saved historical trade records retain lots, lot size, quantity, entry/exit, P&L and exit reason.
- Paper trading only. No Angel One order-placement API is called by historical trading.

## Run
Use the same startup/deployment process as the previous TradeLab build. Render continues to use `DATABASE_URL` for Neon persistence when configured.


## v2.11.3 live-market UX polish
- Stable live chart Y-axis viewport with edge-triggered recentering.
- Compact collision-aware strategy signal labels on live charts.
- Responsive live position overlay with entry-to-LTP price movement.
- Clear INDEX vs NSE FO instrument header metadata.
- Existing paper-only execution and lots/P&L engine preserved.
