# FunTrade

Research simulator for **European UCITS ETFs** using a perturbation-theory model ported from [per-trade](../per-trade): H₀ equilibrium + H₁ deviation signals (ε), backtests, paper portfolio, and Grafana observability.

**Paper only in v1** — no broker connectivity. Prove the model on public daily data before IBKR or similar.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (TimescaleDB + Grafana)
- [uv](https://docs.astral.sh/uv/) (Python 3.12+)
- Make

## Quick start

```bash
# 1. Config
cp .env.example .env          # edit WATCHLIST if needed

# 2. Infrastructure
make setup                    # install Python deps
make run                      # TimescaleDB :5433, Grafana :3001

# 3a. Offline demo (no market API)
make seed                     # synthetic daily bars
make calibrate SYMBOL=VWCE.DE
make detect
make backtest SYMBOL=VWCE.DE
make ui                       # http://localhost:8501

# 3b. Live data (needs network)
make ingest
make ingest-factors
make calibrate SYMBOL=VWCE.DE
make backtest SYMBOL=VWCE.DE
```

One-liner offline demo:

```bash
make setup && make run && make demo SYMBOL=VWCE.DE
```

## Makefile commands

| Command | Description |
|---------|-------------|
| `make help` | List all targets |
| `make setup` | Copy `.env.example` → `.env`, install deps |
| `make run` / `make run-down` | Start / stop Docker stack |
| `make seed` | Synthetic price data (CI / offline) |
| `make ingest` | Watchlist daily bars (Stooq → yfinance fallback) |
| `make ingest-factors` | H₀ macro series (EUR/USD, rates, credit spread) |
| `make calibrate SYMBOL=VWCE.DE` | Fit H₀ OU equilibrium |
| `make detect` | Latest ε per watchlist symbol |
| `make backtest SYMBOL=VWCE.DE` | Walk-forward backtest |
| `make sweep SYMBOL=VWCE.DE` | ε threshold sweep |
| `make compare SYMBOL=VWCE.DE` | Strategy vs EXSA.DE buy-and-hold |
| `make paper SYMBOL=VWCE.DE` | Forward paper trade (simulated) |
| `make ui` | Streamlit console |
| `make test` | pytest (no network) |
| `make reconcile SYMBOL=VWCE.DE` | Stooq vs EOD price check |

Override defaults: `SYMBOL=VWCE.DE DAYS=730 make ingest`

## Architecture

```
price/factor ingest → TimescaleDB
        ↓
  H₀ calibrate (OU + seasonality + macro adjustment)
        ↓
  H₁ detect (ε = weighted z-scores)
        ↓
  backtest / paper wallet / Streamlit UI
```

**H₁ inputs (ε blend):** `z_return` (vs equilibrium), `z_volume`, `z_rel_strength` (vs sector ETF), `z_vol`.

**Trade rule:** long-only mean reversion — buy when ε < −threshold, sell to exit when ε > +threshold, only if `regime_valid`.

See [fun-trade-plan.md](fun-trade-plan.md) and [docs/component-model.md](docs/component-model.md) for design detail.

## Default universe

| Symbol | Role |
|--------|------|
| `EXSA.DE` | Benchmark (STOXX Europe 600) |
| `VWCE.DE` | Global equity |
| `EUNL.DE` | US equity |
| `IS3N.DE` | MSCI World |
| `SXR8.DE` | S&P 500 |
| `AGGH.DE` | Aggregate bonds |
| `IBCI.DE` | Euro gov bonds (rates proxy) |

Configure in `.env`:

```bash
BENCHMARK=EXSA.DE
WATCHLIST=EXSA.DE,VWCE.DE,EUNL.DE,IS3N.DE,SXR8.DE,AGGH.DE,IBCI.DE
CURRENCY=EUR
EPSILON_THRESHOLD=2.0
```

## Services

| Service | URL |
|---------|-----|
| TimescaleDB | `postgresql://funtrade:funtrade@localhost:5433/funtrade` |
| Grafana | http://localhost:3001 (admin / admin) |
| Streamlit UI | http://localhost:8501 |

## Python CLIs

Installed via `uv sync` in `python/`:

- `funtrade-ingest`, `funtrade-ingest-factors`
- `funtrade-calibrate`, `funtrade-detect`
- `funtrade-backtest`, `funtrade-paper`
- `funtrade-ui`, `funtrade-reconcile`, `funtrade-jacobian`

## Paper mode vs live trading

**Paper mode** (default) writes simulated fills to TimescaleDB — no real orders. Forward paper (`make paper`) runs the live signal path on the latest bar.

Live broker execution (IBKR, etc.) is documented in [docs/future-paths.md](docs/future-paths.md) and not implemented in v1.

## Data providers

1. **Stooq** — primary when accessible
2. **yfinance** — automatic fallback for ingest
3. **EOD Historical Data** — optional reconcile (`EOD_API_TOKEN` in `.env`)
4. **`make seed`** — synthetic data for offline dev

Details: [docs/data-providers.md](docs/data-providers.md)

## Development

```bash
make test                     # unit tests
make clean                    # clear caches
```

## Related

- [per-trade](../per-trade) — Nord Pool power-market reference implementation
- [docs/lessons-from-pertrade.md](docs/lessons-from-pertrade.md) — what we changed for ETFs
