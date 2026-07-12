-- Regime router labels persisted at detect time (trending / ranging / uncertain).
ALTER TABLE perturbation_daily
  ADD COLUMN IF NOT EXISTS market_regime TEXT,
  ADD COLUMN IF NOT EXISTS selected_model TEXT;
