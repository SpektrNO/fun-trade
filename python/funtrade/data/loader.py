"""Load and save price bars and model outputs from TimescaleDB."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from funtrade.config import Settings, get_connection, read_sql_df

MARKET_ADJ_CLOSE = "adj_close"


@dataclass(frozen=True)
class PerturbationSnapshot:
    """Latest persisted ε row for one symbol (from detect / make refresh)."""

    time: pd.Timestamp
    symbol: str
    asset_class: str
    epsilon: float
    magnitude: float
    regime_valid: bool
    price: float | None
    z_return: float | None
    z_volume: float | None
    z_rel_strength: float | None
    z_trend: float | None
    market_regime: str | None
    selected_model: str | None
    computed_at: pd.Timestamp | None


def load_latest_perturbation_snapshots(
    symbols: list[str] | None = None,
    *,
    settings: Settings | None = None,
) -> dict[str, PerturbationSnapshot]:
    """One latest row per symbol from perturbation_daily — fast path for recommendations."""
    settings = settings or Settings.from_env()
    symbols = symbols or settings.watchlist
    if not symbols:
        return {}

    query = """
        SELECT DISTINCT ON (symbol)
          time, symbol, asset_class, epsilon, magnitude, regime_valid,
          z_return, z_volume, z_rel_strength, price, z_trend,
          market_regime, selected_model, computed_at
        FROM perturbation_daily
        WHERE symbol = ANY(%(symbols)s)
        ORDER BY symbol, time DESC
    """
    fallback_query = """
        SELECT DISTINCT ON (symbol)
          time, symbol, asset_class, epsilon, magnitude, regime_valid,
          z_return, z_volume, z_rel_strength, price, z_trend, computed_at
        FROM perturbation_daily
        WHERE symbol = ANY(%(symbols)s)
        ORDER BY symbol, time DESC
    """
    legacy_query = """
        SELECT DISTINCT ON (symbol)
          time, symbol, asset_class, epsilon, magnitude, regime_valid,
          z_return, z_volume, z_rel_strength, price, computed_at
        FROM perturbation_daily
        WHERE symbol = ANY(%(symbols)s)
        ORDER BY symbol, time DESC
    """
    params = {"symbols": symbols}
    has_z_trend = True
    has_regime = True
    try:
        df = read_sql_df(query, params, settings=settings)
    except Exception as exc:
        msg = str(exc).lower()
        if "market_regime" in msg or "selected_model" in msg:
            has_regime = False
            try:
                df = read_sql_df(fallback_query, params, settings=settings)
            except Exception as exc2:
                if "z_trend" not in str(exc2).lower():
                    raise
                has_z_trend = False
                df = read_sql_df(legacy_query, params, settings=settings)
        elif "z_trend" in msg:
            has_z_trend = False
            has_regime = False
            df = read_sql_df(legacy_query, params, settings=settings)
        else:
            raise
    if df.empty:
        return {}

    out: dict[str, PerturbationSnapshot] = {}
    for row in df.itertuples(index=False):
        sym = str(row.symbol)
        out[sym] = PerturbationSnapshot(
            time=pd.Timestamp(row.time),
            symbol=sym,
            asset_class=str(row.asset_class or "etf"),
            epsilon=float(row.epsilon),
            magnitude=float(row.magnitude),
            regime_valid=bool(row.regime_valid),
            price=float(row.price) if row.price is not None else None,
            z_return=float(row.z_return) if row.z_return is not None else None,
            z_volume=float(row.z_volume) if row.z_volume is not None else None,
            z_rel_strength=float(row.z_rel_strength) if row.z_rel_strength is not None else None,
            z_trend=float(row.z_trend) if has_z_trend and getattr(row, "z_trend", None) is not None else None,
            market_regime=str(row.market_regime) if has_regime and getattr(row, "market_regime", None) else None,
            selected_model=str(row.selected_model) if has_regime and getattr(row, "selected_model", None) else None,
            computed_at=pd.Timestamp(row.computed_at) if row.computed_at is not None else None,
        )
    return out


def trade_date_index(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """UTC midnight keyed by the provider's local trading calendar date (Yahoo trade date)."""
    if index.empty:
        return index
    dates: list[str] = []
    for ts in index:
        if ts.tzinfo is not None:
            dates.append(ts.strftime("%Y-%m-%d"))
        else:
            dates.append(pd.Timestamp(ts).normalize().strftime("%Y-%m-%d"))
    return pd.DatetimeIndex(dates, tz="UTC")


def normalize_daily_bars(df: pd.DataFrame) -> pd.DataFrame:
    """One row per local trading day (last close, summed volume)."""
    if df.empty:
        return df
    df = df.copy()
    df.index = trade_date_index(pd.DatetimeIndex(df.index))
    grouped = df.groupby(df.index)
    agg: dict[str, tuple[str, str]] = {}
    for col in df.columns:
        if col == "volume":
            agg[col] = (col, "sum")
        else:
            agg[col] = (col, "last")
    return grouped.agg(**agg).sort_index()


def load_price_bars(
    symbol: str,
    market: str = MARKET_ADJ_CLOSE,
    *,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    settings: Settings | None = None,
) -> pd.DataFrame:
    settings = settings or Settings.from_env()
    query = """
        SELECT time, symbol, market, price, volume, source
        FROM price_bars
        WHERE symbol = %(symbol)s AND market = %(market)s
    """
    params: dict = {"symbol": symbol, "market": market}

    if start is not None:
        query += " AND time >= %(start)s"
        params["start"] = start
    if end is not None:
        query += " AND time <= %(end)s"
        params["end"] = end

    query += " ORDER BY time ASC"

    df = read_sql_df(query, params, settings=settings)
    if df.empty:
        return df

    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time")
    return normalize_daily_bars(df)


def load_price_bars_batch(
    symbols: list[str],
    *,
    market: str = MARKET_ADJ_CLOSE,
    tail_bars: int | None = None,
    settings: Settings | None = None,
) -> dict[str, pd.DataFrame]:
    """Load price bars for many symbols in one query (optional tail per symbol)."""
    if not symbols:
        return {}
    settings = settings or Settings.from_env()
    if tail_bars is not None:
        query = """
            WITH ranked AS (
              SELECT time, symbol, market, price, volume, source,
                     ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY time DESC) AS rn
              FROM price_bars
              WHERE symbol = ANY(%(symbols)s) AND market = %(market)s
            )
            SELECT time, symbol, market, price, volume, source
            FROM ranked
            WHERE rn <= %(tail_bars)s
            ORDER BY symbol, time ASC
        """
        params: dict = {"symbols": symbols, "market": market, "tail_bars": int(tail_bars)}
    else:
        query = """
            SELECT time, symbol, market, price, volume, source
            FROM price_bars
            WHERE symbol = ANY(%(symbols)s) AND market = %(market)s
            ORDER BY symbol, time ASC
        """
        params = {"symbols": symbols, "market": market}

    df = read_sql_df(query, params, settings=settings)
    if df.empty:
        return {}

    df["time"] = pd.to_datetime(df["time"], utc=True)
    out: dict[str, pd.DataFrame] = {}
    for sym, grp in df.groupby("symbol"):
        frame = grp.set_index("time").drop(columns=["symbol"], errors="ignore")
        out[str(sym)] = normalize_daily_bars(frame)
    return out


def upsert_price_bars(
    symbol: str,
    bars: pd.DataFrame,
    *,
    market: str = MARKET_ADJ_CLOSE,
    source: str = "stooq",
    settings: Settings | None = None,
) -> int:
    if bars.empty:
        return 0

    bars = normalize_daily_bars(bars.copy())
    settings = settings or Settings.from_env()
    count = 0
    with get_connection(settings) as conn:
        with conn.cursor() as cur:
            for ts, row in bars.iterrows():
                close = float(row.get("close", row.get("price", 0)))
                vol = row.get("volume")
                vol_val = float(vol) if vol is not None and pd.notna(vol) else None
                cur.execute(
                    """
                    INSERT INTO price_bars (time, symbol, market, price, volume, source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (time, symbol, market) DO UPDATE SET
                      price = EXCLUDED.price,
                      volume = EXCLUDED.volume,
                      source = EXCLUDED.source
                    """,
                    (ts.to_pydatetime(), symbol, market, close, vol_val, source),
                )
                count += 1
        conn.commit()
    return count


def save_equilibrium_params(
    symbol: str,
    kappa: float,
    mu: float,
    sigma: float,
    half_life_days: float,
    seasonal_coeffs: dict,
    *,
    settings: Settings | None = None,
) -> None:
    settings = settings or Settings.from_env()
    with get_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO equilibrium_params
                    (symbol, kappa, mu, sigma, half_life_days, seasonal_coeffs)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (symbol, kappa, mu, sigma, half_life_days, json.dumps(seasonal_coeffs)),
            )
        conn.commit()


def load_latest_equilibrium_params(
    symbol: str,
    *,
    settings: Settings | None = None,
) -> dict | None:
    settings = settings or Settings.from_env()
    with get_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT kappa, mu, sigma, half_life_days, seasonal_coeffs, calibrated_at
                FROM equilibrium_params
                WHERE symbol = %s
                ORDER BY calibrated_at DESC
                LIMIT 1
                """,
                (symbol,),
            )
            row = cur.fetchone()

    if row is None:
        return None

    coeffs = row[4]
    if isinstance(coeffs, str):
        coeffs = json.loads(coeffs)

    return {
        "kappa": row[0],
        "mu": row[1],
        "sigma": row[2],
        "half_life_days": row[3],
        "seasonal_coeffs": coeffs,
        "calibrated_at": row[5],
    }


def save_perturbation_event(
    detected_at,
    symbol: str,
    magnitude: float,
    epsilon: float,
    inputs: dict,
    regime_valid: bool,
    *,
    settings: Settings | None = None,
) -> None:
    settings = settings or Settings.from_env()
    with get_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO perturbation_events
                    (detected_at, symbol, magnitude, epsilon, inputs, regime_valid)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                """,
                (detected_at, symbol, magnitude, epsilon, json.dumps(inputs), regime_valid),
            )
        conn.commit()


def upsert_perturbation_daily(
    symbol: str,
    series: pd.DataFrame,
    *,
    settings: Settings | None = None,
) -> int:
    """Persist full daily ε series (for Grafana / history). Returns rows upserted."""
    if series.empty:
        return 0

    settings = settings or Settings.from_env()
    asset_class = settings.asset_class or "etf"
    daily = normalize_daily_bars(series)
    default_threshold = float(settings.epsilon_threshold)
    rows: list[tuple] = []
    for ts, row in daily.iterrows():
        t = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        mr = row.get("market_regime") if "market_regime" in daily.columns else None
        sm = row.get("selected_model") if "selected_model" in daily.columns else None
        thr = row.get("epsilon_threshold", default_threshold)
        rows.append(
            (
                t,
                symbol,
                asset_class,
                float(row["epsilon"]),
                float(row["magnitude"]),
                bool(row.get("regime_valid", True)),
                float(row["z_return"]) if pd.notna(row.get("z_return")) else None,
                float(row["z_volume"]) if pd.notna(row.get("z_volume")) else None,
                float(row["z_rel_strength"]) if pd.notna(row.get("z_rel_strength")) else None,
                float(row["price"]) if pd.notna(row.get("price")) else None,
                float(row["z_trend"]) if pd.notna(row.get("z_trend")) else None,
                str(mr) if mr is not None and pd.notna(mr) else None,
                str(sm) if sm is not None and pd.notna(sm) else None,
                float(row["fair_value"]) if pd.notna(row.get("fair_value")) else None,
                float(row["band_lo"]) if pd.notna(row.get("band_lo")) else None,
                float(row["band_hi"]) if pd.notna(row.get("band_hi")) else None,
                float(row["season_alone"]) if pd.notna(row.get("season_alone")) else None,
                float(row["h0_compare"]) if pd.notna(row.get("h0_compare")) else None,
                float(thr) if pd.notna(thr) else default_threshold,
            )
        )

    times = [r[0] for r in rows]
    with get_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO perturbation_daily (
                  time, symbol, asset_class, epsilon, magnitude, regime_valid,
                  z_return, z_volume, z_rel_strength, price, z_trend,
                  market_regime, selected_model,
                  fair_value, band_lo, band_hi, season_alone, h0_compare,
                  epsilon_threshold, computed_at
                )
                VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                )
                ON CONFLICT (time, symbol) DO UPDATE SET
                  asset_class = EXCLUDED.asset_class,
                  epsilon = EXCLUDED.epsilon,
                  magnitude = EXCLUDED.magnitude,
                  regime_valid = EXCLUDED.regime_valid,
                  z_return = EXCLUDED.z_return,
                  z_volume = EXCLUDED.z_volume,
                  z_rel_strength = EXCLUDED.z_rel_strength,
                  price = EXCLUDED.price,
                  z_trend = EXCLUDED.z_trend,
                  market_regime = COALESCE(EXCLUDED.market_regime, perturbation_daily.market_regime),
                  selected_model = COALESCE(EXCLUDED.selected_model, perturbation_daily.selected_model),
                  fair_value = EXCLUDED.fair_value,
                  band_lo = EXCLUDED.band_lo,
                  band_hi = EXCLUDED.band_hi,
                  season_alone = EXCLUDED.season_alone,
                  h0_compare = EXCLUDED.h0_compare,
                  epsilon_threshold = EXCLUDED.epsilon_threshold,
                  computed_at = NOW()
                """,
                rows,
            )
            # Drop bars left from older detects (e.g. pre-H₀-band / pre-σ-floor) so Grafana
            # does not mix eras when the price series coverage changed.
            cur.execute(
                """
                DELETE FROM perturbation_daily
                WHERE symbol = %s
                  AND NOT (time = ANY(%s::timestamptz[]))
                """,
                (symbol, times),
            )
        conn.commit()
    return len(rows)


def save_backtest_run(
    symbol: str,
    epsilon_threshold: float,
    metrics: dict,
    *,
    benchmark_symbol: str | None = None,
    vs_benchmark_return: float | None = None,
    settings: Settings | None = None,
) -> None:
    settings = settings or Settings.from_env()
    with get_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO backtest_runs
                    (symbol, benchmark_symbol, epsilon_threshold, sharpe, max_drawdown,
                     hit_rate, total_trades, vs_benchmark_return, metrics)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    symbol,
                    benchmark_symbol,
                    epsilon_threshold,
                    metrics.get("sharpe"),
                    metrics.get("max_drawdown"),
                    metrics.get("hit_rate"),
                    metrics.get("total_trades"),
                    vs_benchmark_return,
                    json.dumps(metrics),
                ),
            )
        conn.commit()
