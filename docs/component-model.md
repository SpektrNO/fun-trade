# Component model (H₀ / H₁)

See [fun-trade-plan.md](../fun-trade-plan.md) for full design.

**H₀** — slow equilibrium: OU on deseasonalized log(adj close) + month/DOW seasonality + macro adjustment (`eur_rates`, `credit_spread`, `eur_usd`, `sector_beta`).

**H₁** — fast perturbations blended into ε:

| ID | Meaning |
|----|---------|
| `z_return` | OU residual / σ |
| `z_volume` | Volume vs 20d baseline |
| `z_rel_strength` | Return vs sector/benchmark ETF |
| `z_vol` | 20d vol vs 252d baseline |

Trade when `|ε| > EPSILON_THRESHOLD` and `regime_valid` (long-only).
