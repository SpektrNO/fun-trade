-- Daily ε time series for Grafana (one row per symbol per bar date).
CREATE TABLE IF NOT EXISTS perturbation_daily (
  time            TIMESTAMPTZ NOT NULL,
  symbol          TEXT NOT NULL,
  epsilon         DOUBLE PRECISION NOT NULL,
  magnitude       DOUBLE PRECISION NOT NULL,
  regime_valid    BOOLEAN NOT NULL DEFAULT TRUE,
  z_return        DOUBLE PRECISION,
  z_volume        DOUBLE PRECISION,
  z_rel_strength  DOUBLE PRECISION,
  price           DOUBLE PRECISION,
  computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (time, symbol)
);

SELECT create_hypertable('perturbation_daily', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_perturbation_daily_symbol_time
  ON perturbation_daily (symbol, time DESC);
