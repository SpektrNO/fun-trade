"""Regime router: classify trending vs ranging markets and pick perturbation vs momentum."""

from __future__ import annotations

from typing import Literal

import pandas as pd

from funtrade.config import Settings
from funtrade.models.momentum import compute_momentum_series
from funtrade.models.perturbation import compute_perturbation_series
from funtrade.universe_config import (
    MarketRegimeName,
    MomentumBenchmarkConfig,
    StrategyModelName,
    StrategyRouterConfig,
)

MODEL_PERTURBATION: StrategyModelName = "perturbation"
MODEL_MOMENTUM_BENCHMARK: StrategyModelName = "momentum_benchmark"


def count_ma_crossovers(fast_ma: pd.Series, slow_ma: pd.Series, lookback: int) -> pd.Series:
    """Rolling count of fast/slow MA crossovers over the prior `lookback` bars (inclusive)."""
    if fast_ma.empty:
        return pd.Series(dtype=float)
    bullish = (fast_ma > slow_ma).astype(float)
    cross = bullish.diff().abs().fillna(0.0)
    window = max(1, int(lookback))
    return cross.rolling(window, min_periods=1).sum()


def classify_regime_raw(
    *,
    fast_ma: float,
    slow_ma: float,
    momentum: float,
    z_trend: float,
    ma_crosses_recent: float,
    router: StrategyRouterConfig,
    momentum_config: MomentumBenchmarkConfig,
) -> MarketRegimeName:
    """Point-in-time regime label before hysteresis."""
    if pd.isna(fast_ma) or pd.isna(slow_ma):
        return "uncertain"

    mom_ok = True
    if momentum_config.require_momentum_for_buy:
        mom_ok = not pd.isna(momentum) and momentum >= momentum_config.momentum_threshold

    z = 0.0 if pd.isna(z_trend) else float(z_trend)
    crosses = 0.0 if pd.isna(ma_crosses_recent) else float(ma_crosses_recent)

    if abs(z) <= router.range_z_max or crosses >= router.ma_cross_max_for_range:
        return "ranging"

    if fast_ma > slow_ma and mom_ok and z >= router.trend_z_min:
        return "trending"

    return "uncertain"


def model_for_regime(
    regime: MarketRegimeName,
    router: StrategyRouterConfig,
) -> StrategyModelName:
    if regime == "trending":
        return MODEL_MOMENTUM_BENCHMARK
    if regime == "ranging":
        return MODEL_PERTURBATION
    return router.default_model


def apply_regime_hysteresis(
    raw_labels: pd.Series,
    *,
    min_days: int,
) -> pd.Series:
    """Require `min_days` consecutive raw labels before switching stable regime."""
    if raw_labels.empty:
        return raw_labels
    min_days = max(1, int(min_days))
    stable: list[str] = []
    current = str(raw_labels.iloc[0])
    pending: str | None = None
    pending_count = 0

    for label in raw_labels.astype(str):
        if label == current:
            pending = None
            pending_count = 0
            stable.append(current)
            continue
        if label == pending:
            pending_count += 1
        else:
            pending = label
            pending_count = 1
        if pending_count >= min_days:
            current = pending
            pending = None
            pending_count = 0
        stable.append(current)

    return pd.Series(stable, index=raw_labels.index, dtype=object)


def compute_regime_series(
    symbol: str,
    *,
    settings: Settings | None = None,
    perturbation: pd.DataFrame | None = None,
    momentum: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Daily market_regime + selected_model aligned to perturbation series index."""
    settings = settings or Settings.from_env().for_symbol(symbol)
    if settings.universe is None:
        raise ValueError("Regime router requires config.json universe")

    router = settings.universe.strategy_router
    momentum_config = settings.universe.momentum_benchmark

    pert = perturbation if perturbation is not None else compute_perturbation_series(symbol, settings=settings)
    if pert.empty:
        return pd.DataFrame()

    mom = momentum if momentum is not None else compute_momentum_series(
        symbol, settings=settings, config=momentum_config,
    )
    mom = mom.reindex(pert.index).ffill()

    fast_ma = mom["fast_ma"].astype(float)
    slow_ma = mom["slow_ma"].astype(float)
    momentum_vals = mom["momentum"].astype(float)
    z_trend = pert["z_trend"].astype(float) if "z_trend" in pert.columns else pd.Series(0.0, index=pert.index)
    ma_crosses = count_ma_crossovers(fast_ma, slow_ma, router.ma_cross_lookback_days)

    raw: list[MarketRegimeName] = []
    for ts in pert.index:
        raw.append(
            classify_regime_raw(
                fast_ma=float(fast_ma.loc[ts]),
                slow_ma=float(slow_ma.loc[ts]),
                momentum=float(momentum_vals.loc[ts]),
                z_trend=float(z_trend.loc[ts]),
                ma_crosses_recent=float(ma_crosses.loc[ts]),
                router=router,
                momentum_config=momentum_config,
            )
        )

    raw_series = pd.Series(raw, index=pert.index, dtype=object)
    stable = apply_regime_hysteresis(raw_series, min_days=router.regime_min_days)
    selected = stable.map(lambda r: model_for_regime(r, router))  # type: ignore[arg-type]

    return pd.DataFrame(
        {
            "market_regime_raw": raw_series,
            "market_regime": stable,
            "selected_model": selected,
        },
        index=pert.index,
    )


def classify_latest_regime(
    symbol: str,
    *,
    settings: Settings | None = None,
) -> tuple[MarketRegimeName, StrategyModelName]:
    """Latest stable regime + model for recommendations."""
    series = compute_regime_series(symbol, settings=settings)
    if series.empty:
        settings = settings or Settings.from_env().for_symbol(symbol)
        router = settings.universe.strategy_router if settings.universe else None
        default: StrategyModelName = router.default_model if router else MODEL_PERTURBATION
        return "uncertain", default
    last = series.iloc[-1]
    return str(last["market_regime"]), str(last["selected_model"])  # type: ignore[return-value]
