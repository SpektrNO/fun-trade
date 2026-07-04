# Data providers

| Priority | Source | Use |
|----------|--------|-----|
| 1 | Stooq | Primary when accessible |
| 2 | yfinance | Automatic ingest fallback |
| 3 | EOD Historical Data | Reconcile (`EOD_API_TOKEN`) |
| 4 | `make seed` | Synthetic offline data |

Always store **adjusted close** as `market='adj_close'`.
