CREATE TABLE IF NOT EXISTS data_quality_checks (
  id              BIGSERIAL PRIMARY KEY,
  checked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  symbol          TEXT NOT NULL,
  time            TIMESTAMPTZ NOT NULL,
  provider_a      TEXT NOT NULL,
  provider_b      TEXT NOT NULL,
  price_a         DOUBLE PRECISION,
  price_b         DOUBLE PRECISION,
  diff_bps        DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_data_quality_symbol_time
  ON data_quality_checks (symbol, time DESC);

CREATE TABLE IF NOT EXISTS paper_portfolio (
  id            SERIAL PRIMARY KEY,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  cash_eur      DOUBLE PRECISION NOT NULL,
  realized_pnl  DOUBLE PRECISION NOT NULL DEFAULT 0
);

INSERT INTO paper_portfolio (id, cash_eur, realized_pnl)
VALUES (1, 100000, 0)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS paper_trades (
  id              BIGSERIAL PRIMARY KEY,
  executed_at     TIMESTAMPTZ NOT NULL,
  symbol          TEXT NOT NULL,
  side            TEXT NOT NULL,
  qty_shares      DOUBLE PRECISION NOT NULL,
  price           DOUBLE PRECISION NOT NULL,
  fee_eur         DOUBLE PRECISION NOT NULL DEFAULT 0,
  epsilon         DOUBLE PRECISION,
  regime_valid    BOOLEAN,
  signal          INTEGER
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol_time
  ON paper_trades (symbol, executed_at DESC);

CREATE TABLE IF NOT EXISTS paper_positions (
  symbol            TEXT PRIMARY KEY,
  net_qty_shares    DOUBLE PRECISION NOT NULL DEFAULT 0,
  avg_price         DOUBLE PRECISION,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
