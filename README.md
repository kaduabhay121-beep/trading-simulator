# TradeLab v2.12.4 — Live Chart Polling Starvation Fix

Fixes the live Chart / Option Chain remaining blank after startup.

## Root cause fixed
The previous build refreshed `/api/market` every 500 ms while also aborting the previous request whenever a new refresh started. `/api/market` performs candles + option-chain work and can legitimately take longer than 500 ms. The browser therefore kept cancelling requests before any response reached `updateUI()`, leaving the initial hardcoded price visible and `marketCache` null. Switching MKT/LMT then produced `Cannot read properties of null (reading 'chart')`.

## Changes
- Normal `/api/market` refresh is now non-overlapping.
- A normal timer tick returns immediately if a request is already in flight.
- Forced refreshes may cancel the previous request (instrument/tab switch).
- Heavy market refresh interval changed from 500 ms to 2 seconds.
- Live SSE/tick path remains the fast path for price updates.
- Added null-safety to limit-order controls when market data is still loading.
- Startup will no longer starve the first market response.

Paper-only execution remains unchanged. No Angel One order-placement API is called.
