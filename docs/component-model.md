# Component model (H₀ / H₁)

See [fun-trade-plan.md](../fun-trade-plan.md) for full design.

**H₀** — slow equilibrium: OU on deseasonalized log(adj close) + month/DOW seasonality + macro adjustment.

Core H₀ inputs (always on): `eur_rates`, `credit_spread`, `eur_usd`, `sector_beta`.

Optional H₀ inputs (`.env`, off by default):

| ID | Enable | Notes |
|----|--------|-------|
| `oil_price` | `H0_ENABLE_OIL=true` | Ticker via `H0_OIL_TICKER` (default Brent `BZ=F`) |
| `climate_transition` | `H0_ENABLE_CLIMATE=true` | `H0_CLIMATE_MODE=spread` (clean vs fossil) or `single` (one ETF) |

After enabling, run `make ingest-factors` and tune weights (`H0_WEIGHT_OIL`, `H0_WEIGHT_CLIMATE`). List active components: `uv run funtrade-components`.

**H₁** — fast perturbations blended into ε:

| ID | Meaning |
|----|---------|
| `z_return` | OU residual / σ |
| `z_volume` | Volume vs 20d baseline |
| `z_rel_strength` | Return vs sector/benchmark ETF |
| `z_vol` | 20d vol vs 252d baseline |

Trade when `|ε| > EPSILON_THRESHOLD` and `regime_valid` (long-only). Optional **trend expectation** (`TREND_ENABLE`) dampens ε in uptrends and can gate sell exits.

**Tuning parameters:** see [tuning-guide.md](tuning-guide.md) for strategy-oriented presets and full reference.

**Trend (H₂)** — optional via `.env`:

| Setting | Role |
|---------|------|
| `TREND_EPSILON_WEIGHT` | Subtract w×z_trend from ε (uptrend → less positive ε) |
| `TREND_FAIR_VALUE_WEIGHT` | Lift H₀ fair value in uptrends |
| `TREND_GATE_SELLS` + `TREND_GATE_Z` | Hold through rallies — block sells when z_trend high |
