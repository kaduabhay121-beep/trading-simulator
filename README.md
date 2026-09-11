# Trading Simulator — Angel One Fast Live v3

Paper-trading simulator with Angel One SmartAPI live market data. Real Angel One orders are never placed by this app.

### v3 fixes
- SENSEX options now resolve from the BFO instrument master (not NFO), so SENSEX CE/PE charts open and can be traded.
- Added ultra-light `/api/tick` path for the current WebSocket-fed price.
- Live chart tick refresh runs independently from the heavier market/option-chain refresh.
- Local candle updates and countdown keep the chart visually live between market refreshes.
- Full market refresh reduced to 500ms while live tick updates run at 50ms.
- Existing UI, paper orders, positions, journal and funds behavior retained.
