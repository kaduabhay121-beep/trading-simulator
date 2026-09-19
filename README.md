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

## v2.11.6 video-integrity fixes
- Reviewed the full 78.7-second mobile recording frame-by-frame and corrected visible chart/UI issues.
- Fixed live price badge countdown showing a timer after market close; closed/pre-open now show the actual market state.
- Added 10m/30m/1h to local countdown handling.
- Fixed live P&L overlay stretching into a large translucent rectangle by removing conflicting top/bottom positioning; overlay is now compact and anchored to the entry level.
- Live overlay width is capped for mobile and remains horizontally scrollable without covering the whole chart.
- Chart venue badge is now dynamic: NSE/BSE for indices and NFO/BFO for options.
- Closed/pre-open LTP badges now use a neutral/status color instead of implying an active up/down tick.
- Fixed VWAP to reset at each trading date on both backend and historical frontend charts, preventing multi-day VWAP contamination and excessive empty chart space.
- Clarified Paper Bot metric from RISK to RISK LEFT and corrected the ₹10,00,000 capital fallback.
- Updated stale Historical Chart Trading version label and ₹100,000 reset/fallback UI values to ₹10,00,000.
- Paper-only execution remains unchanged; no Angel One order-placement API is called.


## v2.11.7 chart interaction fix
- Smooth fractional horizontal chart panning on touch/mouse instead of waiting for a full candle movement.
- Y-axis auto-fit now smoothly expands and shrinks with the visible candle range.
- Vertical panning now remains effective in both historical and live chart modes.
- Historical replay keeps the user's vertical pan offset while adapting the scale to the visible candles.
- Double-tap/reset and instrument/timeframe changes reset the fractional pan state.

## v2.12.0 — Near-Zero-Latency Push Chart Engine
- Replaced normal 50 ms browser `/api/tick` polling with persistent Server-Sent Events (`/api/tick/stream`).
- Angel One WebSocket remains the primary market-data source; browser receives server-pushed ticks without polling waits.
- Live chart updates are coalesced with `requestAnimationFrame()` so multiple ticks do not cause redundant canvas renders.
- Symbol-specific push streams support NIFTY, SENSEX and active option charts.
- 1m/3m/5m/10m/15m/30m/1h candles are updated locally from the pushed tick stream.
- Live Y-axis now fits the visible candle range immediately in the render frame, allowing both expansion and shrinkage without the previous trailing scale effect.
- Horizontal pan keeps fractional candle displacement and remains independent of live tick delivery.
- No Angel One order-placement API is used; execution remains paper/simulated only.

## v2.12.1 — Chart interaction integrity
- Fixed historical vertical panning being cancelled by a second Y-axis state update.
- Historical chart now keeps its own timeframe/status display and cannot be overwritten by the live countdown timer.
- Preserves fractional horizontal pan and independent chart interaction during historical replay.
- Paper-only; no Angel One order placement.


## v2.12.3 — Live market recovery, New button, expiry and watchlist integrity
- Fixed Chart `+ New` crash after historical replay (`marketCache.chain` was undefined).
- `+ New` now safely exits historical mode, restores the live chart, then opens the strike selector.
- Options tab automatically restores live mode if opened during historical replay.
- Added persistent `lastLiveMarketCache` so Wishlist/Watchlist does not show false ₹0 values during historical replay.
- Wishlist keeps NIFTY 50 and SENSEX defaults and displays the latest known live prices.
- Hardened Angel One instrument-master discovery and option-contract filtering for NFO/BFO.
- Added forced instrument-master refresh when current option contracts/expiries cannot be discovered.
- Option expiry dropdown now remains populated even when live quotes are temporarily unavailable.
- Option-chain empty state is explicit instead of silently blank.
- NIFTY/SENSEX chart recovery remains paper-only and uses real Angel One data; no fake live prices are introduced.


## v2.12.5 — Live chart initial-request starvation fix

- Fixed the live `/api/market` request starvation caused by the browser aborting an in-flight market request every refresh tick.
- Normal background refreshes now leave an in-flight request alone; only explicit user-driven refreshes may cancel a stale request.
- Live chart initialization therefore has time to receive the real historical candle snapshot before SSE tick updates are applied.
- Kept SSE/requestAnimationFrame as the low-latency live update path.
- Background market refresh interval changed from 500 ms to 1000 ms; it no longer competes with the live tick stream.
- No Angel One order-placement API is used. Paper execution remains simulator-only.


## v2.12.5+ RBS/SBR and isolated signals
- RBS is implemented as a structural flip setup: higher-timeframe closing-level breakout, M15 retest/rejection, breakout-volume confirmation when real volume is available, and lower-volume corrective retest.
- SBR is the inverse structural setup and is treated as an exit signal by the long-only live bot; it does not open live shorts.
- No synthetic OI/volume is created. If required real volume is unavailable, the structural RBS/SBR trigger remains unqualified.
- Chart strategy markers are independently toggleable: EMA, RSI, VWAP, Breakout, RBS, and SBR.
- Existing EMA/EMA15/VWAP/volume chart indicators remain independently toggleable.
- RBS/SBR structural calculations use only candles already available to the engine; they do not fabricate missing multi-month history.
