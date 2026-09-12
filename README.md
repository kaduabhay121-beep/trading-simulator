# Trading Simulator — Full Paper Bot v2.1

Paper-only trading simulator with Angel One SmartAPI market data, live WebSocket feed, automated long-only paper bot, risk controls, persistence, backtesting, closed-market replay, and ATM/ITM/OTM option selection.

## Historical/replay reliability fixes
- Uses NSE/BSE index segments correctly from the Angel One instrument master.
- Uses the most recent completed market-session close when the market is closed, avoiding weekend/current-time historical requests.
- Enforces SmartAPI's documented historical-window limits (ONE_MINUTE <= 30 days).
- Throttles historical REST requests to reduce rate-limit failures.
- Surfaces Angel One HTTP/error messages instead of hiding them behind "Failed to fetch historical data".

## Safety
The bot is paper-only. Angel One is used for market data and historical data; no real order-placement API is called.
