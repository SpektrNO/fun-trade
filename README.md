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
make calibrate-all
make detect
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
| `make grafana-reload` | Restart Grafana after dashboard changes |
| `make seed` | Synthetic price data (CI / offline) |
| `make ingest` | Watchlist daily bars (Stooq → yfinance fallback) |
| `make ingest-factors` | H₀ macro series (EUR/USD, rates, credit spread; optional oil/climate via `.env`) |
| `make calibrate SYMBOL=VWCE.DE` | Fit H₀ OU equilibrium (one symbol) |
| `make calibrate-all` | Fit H₀ for entire `WATCHLIST` |
| `make detect` | Latest ε per watchlist symbol |
| `make backtest SYMBOL=VWCE.DE` | Walk-forward backtest |
| `make sweep SYMBOL=VWCE.DE` | ε threshold sweep |
| `make compare SYMBOL=VWCE.DE` | Strategy vs EXSA.DE buy-and-hold |
| `make paper` | Forward paper trade for entire `WATCHLIST` |
| `make paper SYMBOL=VWCE.DE` | Forward paper trade for one symbol |
| `make refresh` | Recent ingest + detect + paper (`REFRESH_DAYS=14`) |
| `make live` | One-shot: ingest + factors + calibrate-all + detect (not a daemon) |
| `make ui` | Streamlit console |
| `make test` | pytest (no network) |
| `make reconcile SYMBOL=VWCE.DE` | Stooq vs EOD price check |

Override defaults: `SYMBOL=VWCE.DE DAYS=730 make ingest` · `REFRESH_DAYS=30 make refresh`

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

**Trade rule:** long-only mean reversion — buy when ε < −threshold and `regime_valid`; sell to exit when ε > +threshold while holding (exit allowed even if regime invalid).

On daily UCITS data, |ε| on the close is often below 0.6; the default threshold is **0.5**. Long-only needs **ε < −threshold** to buy first — if backtest shows zero trades, lower ε in the sidebar or set `EPSILON_THRESHOLD=0.5` in `.env`.

See [fun-trade-plan.md](fun-trade-plan.md) and [docs/component-model.md](docs/component-model.md) for design detail.

## Operating the system (act on signals)

FunTrade does **not** poll markets in the background. Docker (DB + Grafana) stays up after `make run`; everything else is **on-demand** — you run ingest → detect → paper when you want a fresh suggestion.

### First-time setup (live data)

```bash
make setup
make run
make ingest              # ~730 days of prices (WATCHLIST), upserts safely
make ingest-factors      # macro inputs for H₀/H₁
make calibrate-all       # H₀ for every symbol in WATCHLIST
make ui                  # optional → http://localhost:8501
```

Check `.env`: `WATCHLIST`, `EPSILON_THRESHOLD` (default **0.5**), and `PAPER_*` wallet settings.

### Daily refresh (before acting)

Run after the **daily close** is available from Stooq/yfinance (typically evening EU time):

```bash
make run                              # if Docker is not up
make refresh                          # ingest + factors + detect + paper (14 days)
# or step by step:
make ingest DAYS=30                   # refresh recent bars (safe to repeat)
make ingest-factors DAYS=30
make calibrate-all                    # optional daily; weekly is often enough
make detect                           # latest ε for whole watchlist
make paper                            # act: simulate fills for all symbols
# or:  make paper SYMBOL=VWCE.DE
```

Override refresh window: `make refresh REFRESH_DAYS=30`

Or use the **Streamlit UI** (`make ui`): **Trade** tab shows ε and runs a paper cycle; **Wallet** tab shows cash, positions, and PnL.

### What “act on a suggestion” means

The model outputs **ε** and a **signal**, not a free-text recommendation:

| Signal | Condition (long-only) |
|--------|------------------------|
| **Buy (+1)** | ε < −threshold and `regime_valid` |
| **Sell (−1)** | ε > +threshold and you **hold shares** (exit even when regime invalid) |
| **Hold (0)** | \|ε\| ≤ threshold, regime invalid, or sell while flat |

`make paper` and the UI **execute** only when signal ≠ 0 and limits allow (cash, position cap, fees). No trade after `make detect` usually means ε is **inside the band** — normal, not a broken pipeline.

### `make live` vs scheduled updates

`make live` is a **one-shot batch** (ingest → factors → calibrate-all → detect), then it exits. It does not run continuously or poll for new bars.

To update regularly, re-run the daily refresh commands or use cron, e.g. weekdays at 18:30:

```bash
0 18 * * 1-5 cd /path/to/fun-trade && make refresh >> logs/paper.log 2>&1
```

### Full reset (wipe data)

**Database + volumes (cleanest):**

```bash
make run-down
docker compose -f docker-compose.yml down -v
make run
make ingest && make ingest-factors && make calibrate-all
```

**DB rows only** (keep containers): truncate tables via `psql` in the TimescaleDB container, then re-ingest. **Paper wallet only:** UI → Wallet → Reset paper portfolio, or `make clean` for the local CSV.

### Suggested workflow

1. **Refresh** — ingest recent days → detect → paper (or UI Trade tab).
2. **Review** — Wallet tab or `make paper` JSON (`signal`, `fill`).
3. **Research** — Backtest tab to sanity-check threshold before trusting paper signals.

## Default universe

| Symbol | Role |
|--------|------|
| `EXSA.DE` | Benchmark (STOXX Europe 600) |
| `VWCE.DE` | Global equity |
| `EUNL.DE` | US equity |
| `IS3N.DE` | MSCI World |
| `SXR8.DE` | S&P 500 |
| `AGGH.DE` | Global aggregate bonds (yfinance: `EUNA.DE`) |
| `IBCI.DE` | Euro gov bonds (rates proxy) |

Configure in `.env`:

```bash
BENCHMARK=EXSA.DE
WATCHLIST=EXSA.DE,VWCE.DE,EUNL.DE,IS3N.DE,SXR8.DE,AGGH.DE,IBCI.DE,NO0010336977
CURRENCY=EUR
EPSILON_THRESHOLD=0.5
```

### Symbol aliases (ISIN / friendly names)

`WATCHLIST` uses **your** symbol ids (ISIN, short name, or exchange ticker). When Yahoo/Stooq needs a different ticker, map it via built-in aliases or `.env`:

| WATCHLIST id | Fund | Yahoo fetch ticker |
|--------------|------|--------------------|
| `NO0010336977` | DNB Barnefond A | `0P00000O4C.IR` |
| `DNB-BARNE.IR` | DNB Barnefond A (alias) | `0P00000O4C.IR` |

List aliases and whether they are in your watchlist:

```bash
uv run funtrade-symbols
```

Add custom mappings without code changes:

```bash
SYMBOL_ALIASES=NO0010336977=0P00000O4C.IR,MYFUND.XX=0P00001234.IR
```

After adding a symbol, run `make ingest SYMBOL=NO0010336977` then `make calibrate-all`.

Optional H₀ macro (oil/climate) and **trend expectation (H₂)** — off by default; see `.env.example` and [docs/component-model.md](docs/component-model.md). Active components: `uv run funtrade-components`.

## Services

| Service | URL |
|---------|-----|
| TimescaleDB | `postgresql://funtrade:funtrade@localhost:5433/funtrade` |
| Grafana | http://localhost:3001 (admin / admin) — **Dashboards → FunTrade** |
| Streamlit UI | http://localhost:8501 |

## Python CLIs

Installed via `uv sync` in `python/`:

- `funtrade-ingest`, `funtrade-ingest-factors`, `funtrade-symbols`
- `funtrade-calibrate`, `funtrade-detect`
- `funtrade-backtest`, `funtrade-paper`
- `funtrade-ui`, `funtrade-reconcile`, `funtrade-jacobian`

## Paper mode vs live trading

**Paper mode** (default) writes simulated fills to TimescaleDB — no real orders. Forward paper (`make paper`) runs detect + signal logic on the **latest bar** and simulates one trading cycle.

**`make live`** fetches real historical data once; it is not a live feed or broker connection.

Live broker execution (IBKR, etc.) is documented in [docs/future-paths.md](docs/future-paths.md) and not implemented in v1.

## Data providers

1. **Stooq** — primary when accessible
2. **yfinance** — automatic fallback for ingest
3. **EOD Historical Data** — optional reconcile (`EOD_API_TOKEN` in `.env`)
4. **`make seed`** — synthetic data for offline dev

Details: [docs/data-providers.md](docs/data-providers.md)

## Grafana dashboards

Provisioned under **Dashboards → FunTrade** (after `make run`):

| Dashboard | Content |
|-----------|---------|
| **Market Data** | Price, volume, ingested symbols, **H₀ oil & climate** (`factor_signals`) |
| **Perturbation Model** | Daily ε time series (`perturbation_daily`), H₀ calibrations, backtest runs |

The ε chart needs **`make detect`** (or `make refresh`) — each detect upserts the full daily ε history per symbol. The old `perturbation_events` table only stores the latest snapshot per run (one point if you ran detect once).

If you upgraded from an older DB, apply migrations then re-detect:

```bash
make migrate
make detect
make grafana-reload
```
| **Paper Trading** | Portfolio, positions, fills |

If dashboards are missing (empty Grafana), reload provisioning:

```bash
make grafana-reload
```

Requires data in TimescaleDB (`make ingest`, `make detect`, `make paper` for model/paper panels).

## Development

```bash
make test                     # unit tests
make clean                    # clear caches
```

## Related

- [per-trade](../per-trade) — Nord Pool power-market reference implementation
- [docs/lessons-from-pertrade.md](docs/lessons-from-pertrade.md) — what we changed for ETFs
