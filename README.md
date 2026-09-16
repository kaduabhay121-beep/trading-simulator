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


## v2.11.4 Live Market Data & Chart Integrity Fix

- Option chart change/percentage is calculated from the option's own previous close, not the NIFTY/SENSEX spot price.
- Live position overlay uses the selected chart instrument's authoritative live LTP and recalculates live price movement and P&L from entry × quantity.
- Option/index chart metadata explicitly identifies instrument kind and exchange.
- Added a display-only candle integrity guard so an implausible corrupted option OHLC outlier cannot stretch the live Y-axis into thousands/negative values. Raw market data is not modified.
- Fast `/api/tick` carries previous close/change metadata from cached quote/chart state without adding a REST request to the low-latency tick loop.
- Paper execution only; no Angel One order-placement API is used by the simulator.

## v2.11.5 — Live Market Integrity Complete Fix
- Atomic live chart/instrument switching with stale-response protection.
- One active instrument state for chart, tick stream and position overlay.
- Live viewport reset/shrink protection against stale bracket/order ranges.
- Option chart Y-axis excludes distant risk/order levels that would destroy readability.
- Same-candle strategy signals are grouped to reduce marker/label collisions.
- Live candle mutation is limited to the active exchange session; no synthetic post-close candles.
- Market status now shows PRE-OPEN / OPEN / POST-CLOSE / CLOSED.
- New live paper orders are blocked outside the applicable regular session; existing positions remain exit-able.
- Pending intraday limit orders do not fill outside market hours.
- Option-chain OI now reads Angel One's `opnInterest` field; unsupported change-in-OI is shown as unavailable rather than zero.
- Live tick timeframe bucketing supports 1m/3m/5m/10m/15m/30m/1h.
- Index header no longer duplicates `INDEX`; venue is shown as NSE/BSE.
- Paper-only execution remains unchanged; no Angel One order-placement API is called.
