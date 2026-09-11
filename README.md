# Trading Simulator — Angel One Live Market Data

This build keeps the existing `index.html` UI and paper-trading functionality. The market-data layer has been switched to Angel One SmartAPI when `ANGELONE_ENABLED=1`.

## Safety
- Angel One is used for **market data only**.
- The simulator does not call Angel One order-placement APIs.
- Buy/Sell/Limit/Exit actions remain virtual paper trades inside this app.
- Never put Angel One credentials in `index.html`, JavaScript, GitHub, or browser storage.

## Render environment variables
Set these as **secret Environment Variables** in Render:

```text
ANGELONE_ENABLED=1
ANGELONE_API_KEY=...
ANGELONE_CLIENT_CODE=...
ANGELONE_PIN=...
ANGELONE_TOTP_SECRET=...
```

Optional:

```text
ANGELONE_NIFTY_TOKEN=99926000
ANGELONE_SENSEX_TOKEN=99919000
ANGELONE_CLIENT_LOCAL_IP=127.0.0.1
ANGELONE_CLIENT_PUBLIC_IP=0.0.0.0
ANGELONE_MAC=00:00:00:00:00:00
```

The app generates the TOTP server-side and keeps the resulting JWT/feed token in process memory.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Without Angel credentials, the app remains in clearly separated development mode. With the four Angel credentials above and `ANGELONE_ENABLED=1`, live Angel One data is used.

## Market data
The backend uses Angel One's instrument master, live quote API, historical candle API and option Greeks API. The option chain is assembled from Angel One's current option contracts and live quotes rather than generated values.
