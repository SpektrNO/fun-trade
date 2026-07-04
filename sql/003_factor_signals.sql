CREATE TABLE IF NOT EXISTS factor_signals (
  time        TIMESTAMPTZ NOT NULL,
  series_id   TEXT NOT NULL,
  component   TEXT NOT NULL,
  role        TEXT NOT NULL CHECK (role IN ('h0', 'h1')),
  value       DOUBLE PRECISION NOT NULL,
  unit        TEXT,
  source      TEXT NOT NULL DEFAULT 'stooq',
  PRIMARY KEY (time, series_id, component)
);

SELECT create_hypertable('factor_signals', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_factor_signals_series_role
  ON factor_signals (series_id, role, time DESC);
