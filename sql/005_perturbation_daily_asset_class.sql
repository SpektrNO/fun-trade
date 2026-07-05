-- Asset class label per symbol (etf | mutual_fund | share) from config.json.
ALTER TABLE perturbation_daily
  ADD COLUMN IF NOT EXISTS asset_class TEXT;

CREATE INDEX IF NOT EXISTS idx_perturbation_daily_asset_class
  ON perturbation_daily (asset_class, symbol, time DESC);
