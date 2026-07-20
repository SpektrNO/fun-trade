-- Observation series used for H₀ distance (fair * exp(residual)), for Grafana dual-axis plots.
ALTER TABLE perturbation_daily
  ADD COLUMN IF NOT EXISTS h0_compare DOUBLE PRECISION;
