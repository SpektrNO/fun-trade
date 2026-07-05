"""Library-agnostic chart data preparation."""

from __future__ import annotations

import pandas as pd


def prepare_trade_chart_frames(
    series: pd.DataFrame,
    *,
    epsilon_threshold: float,
    trend_enable: bool = False,
    trend_gate_z: float | None = None,
) -> dict[str, pd.DataFrame]:
    """Build per-panel DataFrames for trade-tab charts."""
    chart = series.reset_index().rename(columns={"index": "time"})
    chart["time"] = pd.to_datetime(chart["time"])

    eps = chart[["time", "epsilon"]].copy()
    eps["upper"] = epsilon_threshold
    eps["lower"] = -epsilon_threshold
    eps["zero"] = 0.0

    out: dict[str, pd.DataFrame] = {
        "epsilon": eps,
        "price": chart[["time", "price"]].copy(),
    }

    if trend_enable and "z_trend" in chart.columns:
        zt = chart[["time", "z_trend"]].copy()
        if trend_gate_z is not None:
            zt["gate"] = trend_gate_z
            zt["neg_gate"] = -trend_gate_z
        out["z_trend"] = zt

    return out
