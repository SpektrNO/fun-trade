-- Seasonal H₀ component alone (exp(season), no μ / macro / trend) for Grafana.
ALTER TABLE perturbation_daily
  ADD COLUMN IF NOT EXISTS season_alone DOUBLE PRECISION;
