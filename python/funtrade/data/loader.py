"""Load and save price bars and model outputs from TimescaleDB."""

from __future__ import annotations

import json

import pandas as pd

from funtrade.config import Settings, get_connection, read_sql_df

MARKET_ADJ_CLOSE = "adj_close"


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
    rows: list[tuple] = []
    for ts, row in daily.iterrows():
        t = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
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
            )
        )

    with get_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO perturbation_daily (
                  time, symbol, asset_class, epsilon, magnitude, regime_valid,
                  z_return, z_volume, z_rel_strength, price, computed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (time, symbol) DO UPDATE SET
                  asset_class = EXCLUDED.asset_class,
                  epsilon = EXCLUDED.epsilon,
                  magnitude = EXCLUDED.magnitude,
                  regime_valid = EXCLUDED.regime_valid,
                  z_return = EXCLUDED.z_return,
                  z_volume = EXCLUDED.z_volume,
                  z_rel_strength = EXCLUDED.z_rel_strength,
                  price = EXCLUDED.price,
                  computed_at = NOW()
                """,
                rows,
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
