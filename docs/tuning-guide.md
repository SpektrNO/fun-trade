# Parameter tuning guide

How to configure FunTrade for your strategy. Parameters live in three places:

| Where | What it controls | Persists? |
|-------|------------------|-----------|
| **`config.json`** | Watchlist, per-asset-class trading rules (`etf` / `mutual_fund` / `share`) | Yes — used by CLI, paper, detect, Recommendations |
| **`.env`** | Database, H₀ macro factors, global trend on/off, paper/backtest wallet, chart backend | Yes — restart UI/CLI after edits |
| **Streamlit sidebar** | ε threshold, H₁ weights, optional macro/trend sliders, chart alignment | Session only — overrides `config.json` for Backtest / Trade / current symbol |

**Signal reminder (long-only mean reversion):**

- **BUY** — ε &lt; −threshold and `regime_valid`
- **SELL** — ε &gt; +threshold and you **hold shares** (exit allowed even when regime invalid)
- **HOLD** — inside the band, flat with positive ε, regime invalid on buys, or sell gated in uptrend

ε is computed daily from price vs H₀ equilibrium plus H₁ stress terms. See [component-model.md](component-model.md) for the model stack.

After changing `config.json` or `.env`, run `make detect` (or `make refresh`) so Recommendations and Grafana pick up new logic. H₀ changes need `make calibrate-all`.

---

## If you want more buy/sell signals (tactical overlay)

Use when you want the model to react to smaller dips and rallies — e.g. trimming or adding on a monthly basis.

| Parameter | Where | Direction | Effect |
|-----------|-------|-----------|--------|
| **`epsilon_threshold`** | UI slider / `config.json` | **Lower** (e.g. 0.75 → 0.50) | Trade on smaller \|ε\|; more BUY and SELL days |
| **`w_return`** | UI slider / `config.json` | **Raise** (e.g. 0.35 → 0.50) | ε tracks price vs equilibrium more; faster reaction to pullbacks |
| **`w_rel_strength`** | UI slider / `config.json` | **Raise** | More weight on “beaten down vs sector” days → more buy signals on relative weakness |
| **`w_volume`** | UI slider / `config.json` | **Raise** (ETFs) | Volume spikes add to ε; can trigger sooner on stress days |
| **`regime_spike_sigma`** | `config.json` only | **Raise** (e.g. 3.0 → 4.0) | Fewer “regime invalid” halts → fewer blocked dip-buys |
| **`regime_consecutive_bars`** | `config.json` only | **Raise** (e.g. 3 → 5) | Require longer stress before halting buys |
| **`trend_gate_sells`** | `config.json` / UI (if `TREND_ENABLE`) | **false** | Allow trim signals even in uptrends |
| **`trend_epsilon_weight`** | `config.json` / UI | **Lower** | Less dampening of ε in bull markets |

**Workflow:** lower threshold in the sidebar → **Run backtest** → check trade count and max \|ε\| on the test period. The UI suggests a quantile-based threshold when there are zero trades.

**Caveat:** More signals ≠ better returns. Lower thresholds increase turnover and fee drag (`PAPER_FEE_BPS` / `BACKTEST_FEE_BPS`).

---

## If you want robust long-term hold (trade only extremes)

Use when you already own a strategic allocation (e.g. VWCE + bond ETF) and only want to act on large dislocations — “insurance trimming” and “deep dip adding”.

| Parameter | Where | Direction | Effect |
|-----------|-------|-----------|--------|
| **`epsilon_threshold`** | `config.json` | **Raise** (e.g. 0.75 → 1.0–1.25) | Only act when price is far from fair value |
| **`w_return`** | `config.json` | **Lower** | ε less dominated by day-to-day vs-band noise |
| **`regime_spike_sigma`** | `config.json` | **Lower** (e.g. 3.0 → 2.5) | Halt new buys sooner during sustained stress |
| **`regime_consecutive_bars`** | `config.json` | **Lower** (e.g. 3 → 2) | Faster regime invalidation in crashes |
| **`trend_gate_sells`** | `config.json` | **true** | Block exits while medium-term trend is up |
| **`trend_gate_z`** | `config.json` / UI | **Lower** (e.g. 0.5 → 0.3) | Gate sells on milder uptrends |
| **`trend_epsilon_weight`** | `config.json` / UI | **Raise** (needs `TREND_ENABLE`) | Pull ε down in rallies → fewer false “overbought” sells |
| **`trend_fair_value_weight`** | `config.json` / UI | **Raise** (e.g. 0.0 → 0.15) | Lift H₀ fair value in uptrends → less permanent positive ε |

**Recommendations tab:** enable **“Assume I hold every symbol”** so SELL/HOLD notes reflect a trim overlay, not “flat long-only”.

**Strategy framing:** pair this profile with separate DCA or buy-and-hold for entries. This model answers *when to trim or add*, not *whether to be in equities at all*.

---

## If buys are rare in a steady bull market

Common with mean-reversion on global ETFs: price sits above H₀ for long stretches → positive ε → “Overbought, flat (long-only)” when the paper wallet is empty.

| Parameter | Where | Direction | Effect |
|-----------|-------|-----------|--------|
| **`epsilon_threshold`** | Lower | More buy signals on shallow pullbacks |
| **`trend_fair_value_weight`** | Raise | Fair value chases the trend; reduces structurally positive ε |
| **`h0_calibration_days`** | `config.json` | **Shorter** (e.g. 504 → 365) | H₀ μ and σ adapt faster to recent levels (more reactive, less stable) |
| **`H0_CALIBRATION_DAYS`** | `.env` | Default for new `config.json` blocks | Same as above for defaults |
| **`trend_fair_value_weight`** (mutual funds) | Already 0.15 in defaults | Consider similar for `etf` block | |

**What does *not* help buys (without changing strategy):** raising `w_return` alone — it can increase \|ε\| but bull markets still bias ε positive. There is no momentum “buy strength” channel today.

---

## If you want fewer whipsaws / more stable ε

| Parameter | Where | Direction | Effect |
|-----------|-------|-----------|--------|
| **`epsilon_threshold`** | Raise | Wider no-trade band |
| **`w_rel_strength`** | Lower | Less reaction to one-day vs-benchmark moves |
| **`w_volume`** | Lower | Ignore volume noise (already 0 for mutual funds) |
| **`h0_calibration_days`** | **Longer** (e.g. 730) | Slower-moving H₀; smoother equilibrium band |
| **`H0_FOURIER_HARMONICS`** | `.env` | Keep at 2 (default) | Smooth seasonality; avoid month-boundary ε jumps |
| **`regime_spike_sigma`** | Lower | Stop trading sooner in volatile regimes |

---

## Regime gate and liquidity

Controls when **`regime_valid`** is false (blocks **new buys**; sells while holding can still fire).

| Parameter | Where | Default (ETF) | Meaning |
|-----------|-------|---------------|---------|
| **`regime_spike_sigma`** | `config.json` | 3.0 | \|ε\| must exceed this for consecutive days to flag stress |
| **`regime_consecutive_bars`** | `config.json` | 3 | How many consecutive spike days trigger invalidation |
| **`min_daily_volume_eur`** | `config.json` | 100000 (ETF), 0 (mutual fund) | 20d avg EUR volume below this → invalid. **0** disables the liquidity check (needed for NAV funds with zero volume) |

**Mutual funds:** keep `min_daily_volume_eur: 0` or buys will be blocked with “Buy blocked (regime)” despite valid ε.

---

## H₁ blend weights (`w_return`, `w_volume`, `w_rel_strength`)

Configured per asset class in `config.json`; overridden in the **UI sidebar** for Backtest / Trade.

| Weight | Drives | Typical use |
|--------|--------|-------------|
| **`w_return`** | Price vs H₀ band (normalized residual) | Core mean-reversion; main dial for “how much price matters” |
| **`w_volume`** | Unusual volume vs 20d baseline | ETFs with real volume; set **0** for mutual funds |
| **`w_rel_strength`** | Symbol return minus sector/benchmark ETF | Catches “this fund lagged Europe today” |

Fixed in code (not in UI): **`z_vol`** (20d/252d vol ratio) at weight **0.15** in the ε blend, plus small macro terms if present in stored factor data.

Weights in the sidebar need not sum to 1.0; they are relative contributions to ε.

---

## Trend expectation (H₂)

**Global switch:** `TREND_ENABLE=true` in `.env` (default **false**). Per-class weights live in `config.json`; sliders appear in the UI only when trend is enabled.

| Parameter | Where | Effect |
|-----------|-------|--------|
| **`TREND_LOOKBACK_DAYS`** | `.env` | SMA lookback for z_trend (default 200) |
| **`trend_use_benchmark`** | `config.json` | Use sector/benchmark ETF for trend instead of symbol price (mutual funds: **true**) |
| **`trend_epsilon_weight`** | `config.json` / UI | Subtract `w × z_trend` from ε → less sell urgency in uptrends |
| **`trend_fair_value_weight`** | `config.json` / UI | Add `w × z_trend` to H₀ log fair value → equilibrium rises with trend |
| **`trend_gate_sells`** | `config.json` / UI | When true, block SELL if `z_trend > trend_gate_z` |
| **`trend_gate_z`** | `config.json` / UI | Uptrend strength required to gate sells (default 0.5) |

**Hold-through-rally profile:** `trend_gate_sells: true`, `trend_epsilon_weight: 0.15–0.25`, `TREND_ENABLE=true`.

**Let winners run but still trim at extremes:** raise `trend_gate_z` so only strong uptrends block sells.

---

## H₀ equilibrium and macro (`.env`)

Fair value band: seasonal OU on log price + optional macro adjustment. Calibrated with `make calibrate-all`; stored in DB.

| Parameter | Where | Effect |
|-----------|-------|--------|
| **`h0_calibration_days`** | `config.json` per class | Price history window for calibration (ETF 504, mutual fund 730, share 365) |
| **`H0_CALIBRATION_DAYS`** | `.env` | Default when building new config blocks |
| **`H0_FOURIER_HARMONICS`** | `.env` | Annual seasonality smoothness (default 2) |
| **`H0_WEIGHT_EUR_RATES`** | `.env` | Fair value shift vs euro rates proxy |
| **`H0_WEIGHT_CREDIT_SPREAD`** | `.env` | Credit stress |
| **`H0_WEIGHT_EUR_USD`** | `.env` | FX headwind for US-heavy funds |
| **`H0_WEIGHT_SECTOR_BETA`** | `.env` | Sector residual vs benchmark |
| **`H0_ENABLE_OIL`** / **`H0_WEIGHT_OIL`** | `.env` | Optional oil factor; UI slider when enabled |
| **`H0_ENABLE_CLIMATE`** / **`H0_WEIGHT_CLIMATE`** | `.env` | Optional climate spread or ETF; UI slider when enabled |

Macro series are z-scored over **252 days** before blending into H₀ — slow moving. After enabling oil/climate: `make ingest-factors` then recalibrate.

**UI:** oil/climate weights in the sidebar override `.env` for the session (Recommendations uses these overrides too).

---

## Universe and symbols (`config.json`)

| Field | Meaning |
|-------|---------|
| **`benchmark`** | Default sector ETF for relative strength (e.g. `EXSA.DE`) |
| **`currency`** | Display label for prices (EUR) |
| **`aliases`** | Map your Nordnet/ISIN label → Yahoo/Stooq ticker for ingest |
| **`etf` / `mutual_fund` / `share` → `symbols`** | Watchlist per asset class |

Each asset-class block shares the same parameter names; tune ETFs and mutual funds separately (volume, calibration window, trend benchmark).

---

## Paper wallet and backtest (`.env`)

Not in the UI sidebar; affects Wallet tab, paper runner, and backtest capital.

| Parameter | Effect |
|-----------|--------|
| **`PAPER_INITIAL_CASH_EUR`** | Starting cash |
| **`PAPER_TRADE_SHARES`** | Shares per signal; also used for “assumed holding” qty on Recommendations |
| **`PAPER_FEE_BPS`** / **`BACKTEST_FEE_BPS`** | Transaction cost per trade |
| **`PAPER_POSITION_LIMIT_SHARES`** | Max position size |
| **`BACKTEST_INITIAL_CASH_EUR`** | Backtest starting capital (defaults to paper size) |

---

## UI-only: charts and research alignment

| Control | Effect |
|---------|--------|
| **Symbol** | Switches asset class defaults from `config.json` |
| **H₀ source** | **Saved** (DB, matches live detect) vs **Walk-forward** (train 70% — Backtest research mode) |
| **ε chart window** | Trade tab: last 120 days vs backtest test slice (~30%) |
| **Assume I hold every symbol** | Recommendations: treat flat symbols as long for SELL logic |
| **`FUNTRADE_CHART_BACKEND`** | `.env`: `streamlit` or `plotly` (zoom) |

Sidebar ε threshold and weights apply to **Backtest**, **Trade**, and **Recommendations** (via `UiParams`). They do **not** change persisted `perturbation_daily` until you run detect with CLI defaults — for live Grafana, edit `config.json` and `make detect`.

---

## Example profiles

### A — Nordnet trim overlay (default-ish ETF block)

```json
"epsilon_threshold": 0.75,
"trend_gate_sells": true,
"trend_epsilon_weight": 0.15,
"trend_fair_value_weight": 0.0,
"w_return": 0.35
```

`.env`: `TREND_ENABLE=true`. Recommendations: **assume holding all**.

### B — Active tactical (more trades)

```json
"epsilon_threshold": 0.55,
"w_return": 0.45,
"w_rel_strength": 0.30,
"regime_spike_sigma": 3.5,
"trend_gate_sells": false
```

Validate on Backtest before paper.

### C — Extreme-only (bond + equity core untouched)

```json
"epsilon_threshold": 1.10,
"regime_spike_sigma": 2.5,
"regime_consecutive_bars": 2,
"trend_gate_sells": true,
"trend_fair_value_weight": 0.15
```

### D — Mutual funds (NAV feeds)

```json
"min_daily_volume_eur": 0,
"w_volume": 0.0,
"h0_calibration_days": 730,
"trend_use_benchmark": true,
"trend_fair_value_weight": 0.15
```

---

## Quick reference — all trading parameters

### `config.json` (per `etf` / `mutual_fund` / `share`)

| Key | Default (ETF) | Role |
|-----|---------------|------|
| `symbols` | — | Watchlist |
| `epsilon_threshold` | 0.75 | \|ε\| band for signals |
| `regime_spike_sigma` | 3.0 | Regime stress level |
| `regime_consecutive_bars` | 3 | Days of stress to invalidate |
| `min_daily_volume_eur` | 100000 | Liquidity gate (0 = off) |
| `h0_calibration_days` | 504 | H₀ fit window |
| `w_return` | 0.35 | H₁ price vs band weight |
| `w_volume` | 0.10 | H₁ volume weight |
| `w_rel_strength` | 0.25 | H₁ relative return weight |
| `trend_epsilon_weight` | 0.15 | H₂ ε dampening (needs `TREND_ENABLE`) |
| `trend_fair_value_weight` | 0.0 | H₂ fair value lift |
| `trend_gate_sells` | true | H₂ block sells in uptrend |
| `trend_gate_z` | 0.5 | H₂ gate threshold |
| `trend_use_benchmark` | false | H₂ trend from benchmark ETF |

### `.env` (global)

See [`.env.example`](../.env.example) for database, `H0_*`, `TREND_ENABLE`, `TREND_LOOKBACK_DAYS`, paper/backtest wallet, `FUNTRADE_CHART_BACKEND`, `FUNTRADE_CONFIG`.

### Streamlit sidebar

`epsilon_threshold`, `w_return`, `w_volume`, `w_rel_strength`, optional `h0_weight_oil` / `h0_weight_climate`, optional trend sliders, `h0_source`, `epsilon_chart_window`.

---

## Related docs

- [component-model.md](component-model.md) — H₀ / H₁ / H₂ definitions
- [README.md](../README.md) — setup, make targets, signal rules
