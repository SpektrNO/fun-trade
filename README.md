# FunTrade

Research simulator for **European UCITS ETFs** and mutual funds using a perturbation-theory model ported from [per-trade](../per-trade).

**Paper only in v1** — no broker connectivity. Prove the model on public daily data before IBKR or similar.

## What it is

FunTrade is a **systematic trading assistant**, not a hands-off execution bot. In market terms:

| Category | How FunTrade fits |
|----------|-------------------|
| **Trading assistant / copilot** | Estimates fair value and flags when a fund looks cheap or rich; you decide and place orders (e.g. Nordnet). |
| **Quant research & backtest platform** | Calibrated H₀ equilibrium, daily ε signals, walk-forward backtests, threshold tuning, Grafana. |
| **Tactical overlay** | Meant to sit on top of a strategic buy-and-hold core — add on dips, trim on rallies — not to replace DCA or asset allocation. |
| **Long-only, daily horizon** | Mean-reversion on UCITS daily closes; regime gates and optional trend dampening (H₂). Not HFT, not intraday, **no shorting**. |

**Strategy in one line:** slow **equilibrium fair value (H₀)** plus fast **deviation score (ε)** → buy / hold / trim when price is far below or above the model band.

**Long-only** means **no short positions** (you never sell short when flat). It does **not** mean long holding period — signals are **daily tactical**, typically days to weeks when ε crosses the threshold.

### Momentum vs mean-reversion

Two common systematic styles:

| | **Typical “AI trading bot”** | **FunTrade** |
|---|------------------------------|--------------|
| **Style** | Trend-following / momentum | Mean-reversion to a modeled fair value |
| **Signal** | MA cross, RSI, “price went up” | ε = deviation from H₀ band |
| **Data** | Price (and volume) only | Price + **macro-shifted** equilibrium (rates, credit, FX, sector beta; optional oil/climate) |
| **Buy when** | Strength / breakout | Cheap vs model (ε < −threshold) |
| **Sell when** | Weakness / stop | Rich vs model while **holding** (ε > +threshold) |
| **Delivery** | Broker script, often live-first | Backtest + paper + observability first |

**Momentum** rides strength. **Mean-reversion** fades deviation from fair value. FunTrade is the second.

### How this differs from a script bot

Most LLM-generated “trading bots” are a few hundred lines: golden/death cross, RSI, or similar **price-only** rules with no calibration layer. FunTrade is a different **strategy class** and **pipeline**:

| Typical script bot | FunTrade |
|--------------------|----------|
| Fixed MA / indicator rules | Calibrated **H₀** (seasonal OU + macro adjustment) per symbol |
| Trend-following | **ε** blend → mean-reversion signals |
| Price history only | H₀ shifts with **macro z-scores** (252-day window) |
| Ship to broker API | **Prove on data**: ingest → calibrate → detect → backtest → Grafana |
| Opaque | Tunable weights, stored calibrations, [tuning guide](docs/tuning-guide.md) |

When ε flags a deviation, the system scores **mispricing vs a statistical fair-value band** that incorporates macro factors — not “cheap vs its own 200-day MA” alone. It does **not** claim causal knowledge of *why* the economy moved (earnings, war, etc.); it measures distance from a modeled equilibrium.

### H₀ and ε (perturbation stack)

```mermaid
flowchart LR
  subgraph typical["Typical MA bot"]
    P1[Daily price] --> MA[MA / RSI rules]
    MA --> MOM[Buy strength / sell weakness]
  end
  subgraph funtrade["FunTrade"]
    P2[Daily price] --> H0[H₀ fair value]
    M[Macro factors] --> H0
    H0 --> EPS[ε deviation]
    P2 --> EPS
    EPS --> MR[Buy cheap / trim rich]
  end
```

- **H₀ (slow anchor)** — log-price seasonality + OU mean μ + macro adjustment (EUR rates proxy, credit spread, EUR/USD, sector beta). Optional oil/climate in `.env`. Fair value band around exp(season + μ + macro_adj).
- **ε (fast perturbation)** — weighted z-scores: mainly **price vs band** (`z_return`), plus volume, relative strength vs sector ETF, vol spike. Macro mostly moves **H₀**; ε is “how far price has moved from that band today.”
- **H₂ (optional)** — trend dampening on ε and sell gates in uptrends (`TREND_ENABLE` in `.env`).

Formal component list: [docs/component-model.md](docs/component-model.md). Strategy presets: [docs/tuning-guide.md](docs/tuning-guide.md). **Learning guide:** [docs/trading-guide.md](docs/trading-guide.md).

### What it is not

- A robo-advisor or portfolio allocator  
- An “AI trading bot” that auto-executes on a live broker (v1 is **paper + research**)  
- A real-time or intraday system (daily bars only)  
- Shorting or market-neutral (long-only)  
- A guarantee of alpha — backtest and compare to buy-and-hold yourself  

Pair FunTrade with a **strategic core** (DCA, target weights). Use it as a **tactical overlay**: trim when rich, add when cheap — especially on an existing Nordnet-style portfolio (Recommendations → “Assume I hold every symbol”).

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (TimescaleDB + Grafana)
- [uv](https://docs.astral.sh/uv/) (Python 3.12+)
- Make

## Quick start

```bash
# 1. Config
cp .env.example .env && cp config.json.example config.json   # edit config.json watchlists (local, gitignored)

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
| `make ingest` | Watchlist daily bars (all, `SYMBOL=…`, or `SYMBOLS='A B'`) |
| `make ingest-factors` | H₀ macro series (EUR/USD, rates, credit spread; optional oil/climate via `.env`) |
| `make calibrate SYMBOL=VWCE.DE` | Fit H₀ OU equilibrium (one symbol) |
| `make calibrate-all` | Fit H₀ for entire watchlist (`config.json`) |
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

Override defaults: `SYMBOL=VWCE.DE DAYS=730 make ingest` · `SYMBOLS='VWCE.DE EXSA.DE' DAYS=30 make ingest` · `REFRESH_DAYS=30 make refresh`

## Architecture

```
price/factor ingest → TimescaleDB
        ↓
  H₀ calibrate (seasonal OU + macro adjustment → fair value band)
        ↓
  H₁ detect (ε = weighted deviation z-scores vs H₀)
        ↓
  backtest / paper wallet / Streamlit UI / Grafana
```

**H₁ inputs (ε blend):** `z_return` (vs equilibrium), `z_volume`, `z_rel_strength` (vs sector ETF), `z_vol`.

**Trade rule (long-only mean-reversion):** buy when ε < −threshold and `regime_valid`; sell to **exit a long** when ε > +threshold while holding (no short when flat).

On daily UCITS data, |ε| on the close is often below 0.6; ETF defaults use **0.75** in `config.json`. Long-only needs **ε < −threshold** to buy from cash — if backtest shows zero trades, lower ε in the sidebar or in `config.json`. In steady bull markets, **few buys** is normal; see [tuning-guide.md](docs/tuning-guide.md).

Design detail: [fun-trade-plan.md](fun-trade-plan.md) · [component-model.md](docs/component-model.md)

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

Check `.env` for `DATABASE_URL` and **`PAPER_*`** wallet settings (`PAPER_INITIAL_CASH_EUR`, `PAPER_TRADE_SLICE_PCT`, fees). Trading thresholds and watchlists are in **`config.json`** (your copy stays local — not committed to git).

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

Or use the **Streamlit UI** (`make ui`):

| Tab | Purpose |
|-----|---------|
| **Wallet** | Cash, positions, PnL, recent fills, reset portfolio |
| **Backtest** | Walk-forward test vs buy-and-hold for the selected symbol |
| **Trade** | ε chart, regime, run one paper cycle for the selected symbol |
| **Recommendations** | All watchlist symbols — BUY (green) / SELL (orange) / HOLD; Nordnet-style manual overlay |

Sidebar **Run refresh** runs the same pipeline as `make refresh` (ingest → factors → detect → paper).

### What “act on a suggestion” means

The model outputs **ε** and a **signal**, not a free-text recommendation:

| Signal | Condition (long-only) |
|--------|------------------------|
| **Buy (+1)** | ε < −threshold and `regime_valid` |
| **Sell (−1)** | ε > +threshold and you **hold shares** (exit even when regime invalid) |
| **Hold (0)** | \|ε\| ≤ threshold, regime invalid, or sell while flat |

`make paper` and the UI **execute** only when signal ≠ 0, **`regime_valid`** (for fills), and limits allow — **cash on hand**, position cap, fees. Buys use **fractional shares** sized from a EUR slice (see [Paper wallet](#paper-wallet) below). No trade after `make detect` usually means ε is **inside the band** — normal, not a broken pipeline.

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

1. **Refresh** — ingest recent days → detect → paper (CLI or UI **Run refresh**).
2. **Review** — **Recommendations** tab for the full watchlist, or **Wallet** / `make paper` JSON (`signal`, `fill`).
3. **Research** — **Backtest** tab to sanity-check threshold before trusting paper signals.

### Paper wallet

Forward paper (`make paper`, UI refresh, Trade tab) simulates a **virtual EUR wallet** in TimescaleDB (plus an optional CSV export at `data/paper_trades.csv`).

| Setting | Default | Effect |
|---------|---------|--------|
| `PAPER_INITIAL_CASH_EUR` | 100000 | Starting cash |
| `PAPER_TRADE_SLICE_PCT` | 0.10 | Max **10% of starting cash** per buy/sell tranche (€10k on a €100k wallet) |
| `PAPER_FEE_BPS` | 5 | Fee per fill |
| `PAPER_POSITION_LIMIT_SHARES` | 1000 | Max shares per symbol |

**Sizing rules:**

- Each tranche is a **EUR amount** → **fractional shares** (`slice ÷ price`), not a fixed share count.
- Slice is based on **starting** wallet size (`PAPER_INITIAL_CASH_EUR`), not current NAV.
- **Buys** are capped by **cash still available** — no negative cash.
- **Sells** exit incrementally (one slice per signal), not all-at-once.
- When |ε| is still extreme, slice scales down (smaller steps until mean-reversion target is nearer).

Reset: UI → **Wallet** → Reset paper portfolio. `make clean` removes the CSV only; DB state needs the UI reset.

Backtest uses **`remaining_cash`** slice basis: each buy deploys `BACKTEST_TRADE_SLICE_PCT` of **cash still available** (paper wallet still uses starting-wallet slices via `PAPER_TRADE_SLICE_PCT`).

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

Configure in **`config.json`** (`make setup` copies `config.json.example` on first run; your file is **gitignored** so watchlists stay local):

- **`benchmark`** / **`currency`** — global universe defaults  
- **`etf`**, **`mutual_fund`**, **`share`** — separate symbol lists and trading params (`epsilon_threshold`, regime gates, H₁ weights, trend dampening, **`h0_calibration_days`**)  
- **`aliases`** — watchlist id → Yahoo/Stooq fetch ticker  

**Tuning:** [docs/tuning-guide.md](docs/tuning-guide.md) — what each parameter does, grouped by strategy (“more signals”, “hold long-term”, bull-market buys, etc.).

Example mutual-fund vs ETF difference: `mutual_fund.min_daily_volume_eur: 0` skips the liquidity gate; `mutual_fund.h0_calibration_days: 730` uses a longer NAV history for H₀ than ETFs (`504`).

```bash
make setup    # creates local .env + config.json from examples (config.json is gitignored)
```

### Symbol aliases (ISIN / friendly names)

Watchlist ids use **your** names (ISIN, Nordnet label, or exchange ticker). Map fetch tickers under **`aliases`** in `config.json`:

| Watchlist id | Fund | Yahoo fetch ticker |
|--------------|------|--------------------|
| `NO0010336977` | DNB Barnefond A | `0P00000O4C.IR` |
| `DNB-BARNE.IR` | DNB Barnefond A (alias) | `0P00000O4C.IR` |

List aliases and whether they are in your watchlist:

```bash
uv run funtrade-symbols
```

After adding symbols to `config.json`, ingest them then calibrate:

```bash
make ingest SYMBOLS='NO0010336977 VWCE.DE'
make calibrate-all   # or: make calibrate SYMBOL=VWCE.DE
```

Optional H₀ macro (oil/climate) and **trend expectation (H₂)** — off by default; see `.env.example` and [docs/component-model.md](docs/component-model.md). Active components: `uv run funtrade-components`.

## Services

| Service | URL |
|---------|-----|
| TimescaleDB | `postgresql://funtrade:funtrade@localhost:5433/funtrade` |
| Grafana | http://localhost:3001 (admin / admin) — **Dashboards → FunTrade** |
| Streamlit UI | http://localhost:8501 |

## Remote access with ngrok (optional)

Expose the Streamlit UI on other networks (phone, another Wi‑Fi). Same pattern as [norwegian-honey](../norwegian-honey).

```bash
make ngrok-setup          # once: ngrok.yml + authtoken (copies from ../norwegian-honey if present)
# Edit ngrok.yml: set a reserved *.ngrok-free.dev domain (ngrok dashboard), or skip and use ephemeral URL

make run                  # if DB not up
make ui                   # terminal 1 → http://localhost:8501
make ngrok-tunnel         # terminal 2 → https://YOUR_DOMAIN
make ngrok-url            # print active HTTPS URL
```

Ephemeral URL (no reserved domain): `make ngrok-tunnel-ephemeral` while `make ui` is running.

```bash
make help-ngrok           # all ngrok targets
make ngrok-install        # install ngrok to ~/.local/bin
make ngrok-check          # validate config
```

**Notes:**

- `ngrok.local.yml` and `ngrok.yml` are gitignored; templates are `*.example`.
- Reuse the same ngrok account as norwegian-honey — `make ngrok-setup` copies `../norwegian-honey/ngrok.local.yml` when it exists.
- You need a **separate reserved domain** for FunTrade (port **8501**); do not reuse the honey tunnel domain while both run.
- `python/.streamlit/config.toml` disables CORS/XSRF so the app works behind ngrok (`make ui` runs from `python/`).
- On **ngrok free** mobile browsers: tap **Visit Site** on the interstitial warning page.
- **Blank page on phone?** Open the **root URL only** (e.g. `https://your-name.ngrok-free.dev/`) — not a link preview or `/images/...` path. Streamlit is mobile-friendly; a wrong entry URL loads HTML instead of JS.
- TimescaleDB stays on `localhost:5433` — only the UI is tunneled. Run `make run` on the same machine as `make ui`.

## Python CLIs

Installed via `uv sync` in `python/`:

- `funtrade-ingest` (`--symbol`, `--symbols`, `--days`)
- `funtrade-ingest-factors` (global macro panel, not per-symbol)
- `funtrade-symbols`, `funtrade-calibrate`, `funtrade-detect`
- `funtrade-backtest`, `funtrade-paper` (`--symbol`)
- `funtrade-ui`, `funtrade-reconcile`, `funtrade-jacobian`

## Paper mode vs live trading

**Paper mode** (default) writes simulated fills to TimescaleDB — no real orders. Forward paper (`make paper`) runs detect + signal logic on the **latest bar** and simulates one trading cycle per watchlist symbol (or `SYMBOL=…` for one).

See [Paper wallet](#paper-wallet) for slice sizing and cash rules.

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
| **Paper Trading** | Portfolio, positions, fills |

The ε chart needs **`make detect`** (or `make refresh`) — each detect upserts the full daily ε history per symbol. The old `perturbation_events` table only stores the latest snapshot per run (one point if you ran detect once).

If you upgraded from an older DB, apply migrations then re-detect:

```bash
make migrate
make detect
make grafana-reload
```

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
