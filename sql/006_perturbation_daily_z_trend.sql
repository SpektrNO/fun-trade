-- Optional z_trend at detect time (avoids recomputing on recommendations refresh).
ALTER TABLE perturbation_daily
  ADD COLUMN IF NOT EXISTS z_trend DOUBLE PRECISION;
