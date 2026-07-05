"""Load, store, and derive factor component signals."""

from __future__ import annotations

import numpy as np
import pandas as pd

from funtrade.config import Settings, get_connection, read_sql_df
from funtrade.models.components import (
    ALL_H0_COMPONENTS,
    DEFAULT_H1_WEIGHTS,
    H1_COMPONENTS,
    ComponentRole,
)


def save_factor_signals(
    series_id: str,
    component: str,
    role: str,
    rows: dict,
    *,
    unit: str | None = None,
    source: str = "stooq",
    settings: Settings | None = None,
) -> int:
    if not rows:
        return 0

    settings = settings or Settings.from_env()
    payload = [
        (ts, series_id, component, role, float(val), unit, source)
        for ts, val in rows.items()
    ]
    with get_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO factor_signals (time, series_id, component, role, value, unit, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (time, series_id, component) DO UPDATE SET
                  value = EXCLUDED.value,
                  unit = EXCLUDED.unit,
                  source = EXCLUDED.source,
                  role = EXCLUDED.role
                """,
                payload,
            )
        conn.commit()
    return len(payload)


def load_factor_series(
    series_id: str,
    component: str,
    *,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    settings: Settings | None = None,
) -> pd.Series:
    settings = settings or Settings.from_env()
    query = """
        SELECT time, value FROM factor_signals
        WHERE series_id = %(series_id)s AND component = %(component)s
    """
    params: dict = {"series_id": series_id, "component": component}
    if start is not None:
        query += " AND time >= %(start)s"
        params["start"] = start
    if end is not None:
        query += " AND time <= %(end)s"
        params["end"] = end
    query += " ORDER BY time"

    df = read_sql_df(query, params, settings=settings)
    if df.empty:
        return pd.Series(dtype=float)

    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.set_index("time")["value"].astype(float)


def load_h0_panel(
    series_id: str,
    index: pd.DatetimeIndex,
    *,
    settings: Settings | None = None,
) -> pd.DataFrame:
    settings = settings or Settings.from_env()
    active_ids = set(settings.active_h0_component_ids())
    panel = pd.DataFrame(index=index)
    for comp in ALL_H0_COMPONENTS:
        if comp.id not in active_ids:
            continue
        s = load_factor_series(series_id, comp.id, settings=settings)
        panel[comp.id] = s.reindex(index, method="ffill") if not s.empty else np.nan
    return panel


def _zscore_col(series: pd.Series, window: int = 20) -> pd.Series:
    m = series.rolling(window, min_periods=5).mean()
    s = series.rolling(window, min_periods=5).std().clip(lower=1e-6)
    return ((series - m) / s).fillna(0.0)


def compute_h0_fundamental_adjustment(
    symbol: str,
    index: pd.DatetimeIndex,
    *,
    settings: Settings | None = None,
) -> pd.Series:
    settings = settings or Settings.from_env()
    panel = load_h0_panel("macro", index, settings=settings)

    if panel.isna().all().all():
        panel = load_h0_panel(symbol, index, settings=settings)

    if panel.isna().all().all():
        return pd.Series(0.0, index=index)

    z_panel = panel.apply(_zscore_col, window=252)
    coeffs = settings.h0_weights()

    adj = pd.Series(0.0, index=index)
    for col, w in coeffs.items():
        if col in z_panel.columns:
            adj = adj + w * z_panel[col].fillna(0.0)
    return adj


def compute_h1_component_scores(
    symbol: str,
    index: pd.DatetimeIndex,
    *,
    settings: Settings | None = None,
) -> pd.DataFrame:
    settings = settings or Settings.from_env()
    panel = pd.DataFrame(index=index)
    for comp in H1_COMPONENTS:
        s = load_factor_series(symbol, comp.id, settings=settings)
        if not s.empty:
            panel[comp.id] = _zscore_col(s.reindex(index, method="ffill"))
        else:
            panel[comp.id] = 0.0
    return panel.fillna(0.0)


def blend_epsilon(
    z_return: pd.Series,
    z_volume: pd.Series,
    z_rel_strength: pd.Series,
    h1_scores: pd.DataFrame,
    weights: dict[str, float] | None = None,
) -> pd.Series:
    weights = weights or DEFAULT_H1_WEIGHTS
    eps = (
        weights.get("z_return", 0.35) * z_return
        + weights.get("z_volume", 0.10) * z_volume
        + weights.get("z_rel_strength", 0.25) * z_rel_strength
    )
    for col in h1_scores.columns:
        w = weights.get(col, 0.0)
        if w != 0:
            eps = eps + w * h1_scores[col]
    return eps


def _fetch_close_series(
    provider,
    ticker: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.Series:
    bars = provider.fetch_bars(ticker, start, end)
    if bars.empty:
        return pd.Series(dtype=float)
    return bars["close"].astype(float)


def _ingest_oil_factor(
    provider,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    settings: Settings,
    source: str,
) -> int:
    series = _fetch_close_series(provider, settings.h0_oil_ticker, start, end)
    if series.empty:
        return 0
    rows = {ts.to_pydatetime(): float(v) for ts, v in series.items()}
    return save_factor_signals(
        "macro",
        "oil_price",
        ComponentRole.H0.value,
        rows,
        unit="usd",
        source=source,
        settings=settings,
    )


def _ingest_climate_factor(
    provider,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    settings: Settings,
    source: str,
) -> int:
    if settings.h0_climate_mode == "single":
        series = _fetch_close_series(provider, settings.h0_climate_ticker, start, end)
        if series.empty:
            return 0
        rows = {ts.to_pydatetime(): float(v) for ts, v in series.items()}
        unit = "level"
    else:
        clean = _fetch_close_series(provider, settings.h0_climate_clean_ticker, start, end)
        fossil = _fetch_close_series(provider, settings.h0_climate_fossil_ticker, start, end)
        if clean.empty or fossil.empty:
            return 0
        spread = clean.pct_change().sub(fossil.pct_change(), fill_value=0.0).fillna(0.0)
        rows = {ts.to_pydatetime(): float(v) for ts, v in spread.items() if pd.notna(v)}
        unit = "ratio"

    if not rows:
        return 0
    return save_factor_signals(
        "macro",
        "climate_transition",
        ComponentRole.H0.value,
        rows,
        unit=unit,
        source=source,
        settings=settings,
    )


def ingest_macro_factors(*, days: int = 730, settings: Settings | None = None) -> dict[str, int]:
    """Ingest H0 macro factor series (core always; oil/climate when enabled in .env)."""
    from funtrade.data.ingest import _get_provider
    from funtrade.data.yfinance_provider import YFinancePriceProvider

    settings = settings or Settings.from_env()
    end = pd.Timestamp.now(tz="UTC").normalize()
    start = end - pd.Timedelta(days=days)
    provider = _get_provider()
    yf_provider = YFinancePriceProvider()
    source = "stooq" if provider.__class__.__name__ == "StooqPriceProvider" else "yfinance"
    counts: dict[str, int] = {}

    try:
        bars = provider.fetch_bars("EURUSD", start, end)
        if not bars.empty:
            rows = {ts.to_pydatetime(): float(v) for ts, v in bars["close"].items()}
            counts["eur_usd"] = save_factor_signals(
                "macro", "eur_usd", ComponentRole.H0.value, rows, unit="ratio", source=source, settings=settings
            )
    except Exception:
        counts["eur_usd"] = 0

    try:
        bars = provider.fetch_bars("IBCI.DE", start, end)
        if not bars.empty:
            rows = {ts.to_pydatetime(): float(v) for ts, v in bars["close"].items()}
            counts["eur_rates"] = save_factor_signals(
                "macro", "eur_rates", ComponentRole.H0.value, rows, unit="eur", source=source, settings=settings
            )
    except Exception:
        counts["eur_rates"] = 0

    try:
        agg = provider.fetch_bars("AGGH.DE", start, end)["close"]
        ibc = provider.fetch_bars("IBCI.DE", start, end)["close"]
        spread = (agg.pct_change() - ibc.pct_change()).fillna(0.0)
        rows = {ts.to_pydatetime(): float(v) for ts, v in spread.items() if pd.notna(v)}
        counts["credit_spread"] = save_factor_signals(
            "macro", "credit_spread", ComponentRole.H0.value, rows, unit="ratio", source=source, settings=settings
        )
    except Exception:
        counts["credit_spread"] = 0

    if settings.h0_enable_oil:
        try:
            counts["oil_price"] = _ingest_oil_factor(
                yf_provider, start=start, end=end, settings=settings, source="yfinance"
            )
        except Exception:
            counts["oil_price"] = 0

    if settings.h0_enable_climate:
        try:
            counts["climate_transition"] = _ingest_climate_factor(
                yf_provider, start=start, end=end, settings=settings, source="yfinance"
            )
        except Exception:
            counts["climate_transition"] = 0

    return counts
