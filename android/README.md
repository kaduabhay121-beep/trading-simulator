# TradeLab Android

Native Android, local-first client for the TradeLab research laboratory.

Two explicit modes:
- LIVE MARKET — Angel One supplies genuine market information; paper execution only.
- OFFLINE LAB — no broker connection and no fresh market data. Replay/backtest uses the local Data Vault.

The Android UI is native, not a WebView. The shared Python engine remains the deterministic research/data core while local Android storage becomes the offline-first layer.

Historical NIFTY/SENSEX files do not need to be supplied manually. The first complete sessions captured during market hours become the local research dataset.
