-- H₀ fair value band for Grafana dual-panel charts (price vs fair ± band).
ALTER TABLE perturbation_daily
  ADD COLUMN IF NOT EXISTS fair_value DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS band_lo DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS band_hi DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS epsilon_threshold DOUBLE PRECISION;
