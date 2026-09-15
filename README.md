# TradeLab v2.9.1 — Historical Replay + Permanent Cloud Research Storage

This build adds exact-day historical replay and permanent research/replay storage. It uses SQLite locally by default, and automatically uses PostgreSQL/Neon when `DATABASE_URL` is configured. No Angel One order-placement API is implemented; Angel One is used for market/historical data only.

## Render + Neon
Set the Render environment variable:
`DATABASE_URL=<your Neon PostgreSQL connection string>`

Do not commit the connection string to GitHub or put it in frontend code. Keep it only as a private Render environment variable.

`SIM_DB_PATH` is only used by the local SQLite fallback. Render Free does not need a disk when PostgreSQL is configured.

## Historical retention
Research runs, replay sessions, stored trade details, paper journal entries, and historical datasets are retained permanently by the application. There is no automatic 30-day deletion.
