"""Library-agnostic chart data preparation."""

from __future__ import annotations

import pandas as pd


def normalize_chart_times(series: pd.Series) -> pd.Series:
    """Plotly-friendly UTC-naive timestamps (avoids vrect vs trace axis mismatch)."""
    times = pd.to_datetime(series)
    if getattr(times.dt, "tz", None) is not None:
        times = times.dt.tz_convert("UTC").dt.tz_localize(None)
    return times


def regime_invalid_spans(
    df: pd.DataFrame,
    *,
    time_col: str = "time",
    regime_col: str = "regime_valid",
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Contiguous [start, end] intervals where regime_valid is false."""
    if regime_col not in df.columns or df.empty:
        return []
    plot_df = df.copy()
    plot_df[time_col] = normalize_chart_times(plot_df[time_col])
    valid = plot_df[regime_col].fillna(True).astype(bool)
    times = plot_df[time_col]
    bar = pd.Timedelta(days=1)
    spans: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    in_span = False
    start: pd.Timestamp | None = None
    for ts, ok in zip(times, valid):
        if not ok and not in_span:
            in_span = True
            start = ts
        elif ok and in_span and start is not None:
            in_span = False
            spans.append((start, ts))
            start = None
    if in_span and start is not None:
        spans.append((start, times.iloc[-1] + bar))
    return spans


def prepare_trade_chart_frames(
    series: pd.DataFrame,
    *,
    epsilon_threshold: float,
    trend_enable: bool = False,
    trend_gate_z: float | None = None,
    momentum_overlay: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    """Build per-panel DataFrames for trade-tab charts."""
    chart = series.reset_index().rename(columns={"index": "time"})
    chart["time"] = normalize_chart_times(chart["time"])

    eps_cols = ["time", "epsilon"]
    if "regime_valid" in chart.columns:
        eps_cols.append("regime_valid")
    eps = chart[eps_cols].copy()
    eps["upper"] = epsilon_threshold
    eps["lower"] = -epsilon_threshold
    eps["zero"] = 0.0

    price = chart[["time", "price"]].copy()
    if momentum_overlay is not None and not momentum_overlay.empty:
        overlay = momentum_overlay.copy()
        overlay["time"] = normalize_chart_times(overlay["time"])
        price = price.merge(overlay.drop(columns=["price"], errors="ignore"), on="time", how="left")

    out: dict[str, pd.DataFrame] = {
        "epsilon": eps,
        "price": price,
    }

    if trend_enable and "z_trend" in chart.columns:
        zt = chart[["time", "z_trend"]].copy()
        if trend_gate_z is not None:
            zt["gate"] = trend_gate_z
            zt["neg_gate"] = -trend_gate_z
        out["z_trend"] = zt

    return out


def build_momentum_price_overlay(
    momentum: pd.DataFrame,
    *,
    slow_ma_days: int,
    bollinger_std: float = 2.0,
) -> pd.DataFrame:
    """Fast/slow MA plus Bollinger ±σ bands for the Trade price panel."""
    if momentum.empty:
        return pd.DataFrame()
    min_periods = max(5, slow_ma_days // 4)
    price = momentum["price"].astype(float)
    std = price.rolling(slow_ma_days, min_periods=min_periods).std()
    slow = momentum["slow_ma"].astype(float)
    return pd.DataFrame(
        {
            "time": normalize_chart_times(pd.Series(momentum.index)),
            "Fast MA": momentum["fast_ma"].astype(float).values,
            "Slow MA": slow.values,
            "Upper band (+2σ)": (slow + bollinger_std * std).values,
            "Lower band (−2σ)": (slow - bollinger_std * std).values,
        }
    )


def price_chart_series(df: pd.DataFrame) -> list[str]:
    """Column order for the Trade price panel."""
    preferred = [
        "price",
        "Fair price (H₀)",
        "Fast MA",
        "Slow MA",
        "Upper band (+2σ)",
        "Lower band (−2σ)",
        "Fair + perturbation (ε)",
    ]
    return [col for col in preferred if col in df.columns]
