CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS price_bars (
  time          TIMESTAMPTZ NOT NULL,
  symbol        TEXT NOT NULL,
  market        TEXT NOT NULL,
  price         DOUBLE PRECISION NOT NULL,
  volume        DOUBLE PRECISION,
  source        TEXT NOT NULL DEFAULT 'stooq',
  PRIMARY KEY (time, symbol, market)
);

SELECT create_hypertable('price_bars', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_price_bars_symbol_market_time
  ON price_bars (symbol, market, time DESC);

CREATE TABLE IF NOT EXISTS equilibrium_params (
  id              SERIAL PRIMARY KEY,
  calibrated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  symbol          TEXT NOT NULL,
  kappa           DOUBLE PRECISION NOT NULL,
  mu              DOUBLE PRECISION NOT NULL,
  sigma           DOUBLE PRECISION NOT NULL,
  half_life_days  DOUBLE PRECISION,
  seasonal_coeffs JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_equilibrium_params_symbol
  ON equilibrium_params (symbol, calibrated_at DESC);

CREATE TABLE IF NOT EXISTS perturbation_events (
  id            BIGSERIAL PRIMARY KEY,
  detected_at   TIMESTAMPTZ NOT NULL,
  symbol        TEXT NOT NULL,
  magnitude     DOUBLE PRECISION NOT NULL,
  epsilon       DOUBLE PRECISION NOT NULL,
  inputs        JSONB NOT NULL DEFAULT '{}'::jsonb,
  regime_valid  BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_perturbation_events_symbol
  ON perturbation_events (symbol, detected_at DESC);

CREATE TABLE IF NOT EXISTS backtest_runs (
  id                SERIAL PRIMARY KEY,
  run_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  symbol            TEXT NOT NULL,
  benchmark_symbol  TEXT,
  epsilon_threshold DOUBLE PRECISION NOT NULL,
  sharpe            DOUBLE PRECISION,
  max_drawdown      DOUBLE PRECISION,
  hit_rate          DOUBLE PRECISION,
  total_trades      INTEGER,
  vs_benchmark_return DOUBLE PRECISION,
  metrics           JSONB NOT NULL DEFAULT '{}'::jsonb
);
