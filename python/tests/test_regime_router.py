import numpy as np
import pandas as pd

from funtrade.models.regime_router import (
    apply_regime_hysteresis,
    classify_regime_raw,
    count_ma_crossovers,
    model_for_regime,
)
from funtrade.universe_config import MomentumBenchmarkConfig, StrategyRouterConfig


def _router(**kwargs) -> StrategyRouterConfig:
    defaults = dict(
        trend_z_min=0.5,
        range_z_max=0.3,
        ma_cross_lookback_days=90,
        ma_cross_max_for_range=2,
        regime_min_days=10,
        default_model="perturbation",
    )
    defaults.update(kwargs)
    return StrategyRouterConfig(**defaults)


def _mom_cfg(**kwargs) -> MomentumBenchmarkConfig:
    defaults = dict(
        fast_ma_days=50,
        slow_ma_days=200,
        rsi_period=14,
        rsi_mode="momentum",
        rsi_buy_min=50.0,
        rsi_sell_max=50.0,
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        momentum_lookback_days=63,
        momentum_threshold=0.0,
        require_momentum_for_buy=True,
        exit_on_rsi_weak=True,
        position_mode="scale",
    )
    defaults.update(kwargs)
    return MomentumBenchmarkConfig(**defaults)


def test_classify_regime_trending():
    r = _router()
    m = _mom_cfg()
    assert (
        classify_regime_raw(
            fast_ma=110.0,
            slow_ma=100.0,
            momentum=0.05,
            z_trend=0.8,
            ma_crosses_recent=0.0,
            router=r,
            momentum_config=m,
        )
        == "trending"
    )


def test_classify_regime_ranging_low_z_trend():
    r = _router()
    m = _mom_cfg()
    assert (
        classify_regime_raw(
            fast_ma=110.0,
            slow_ma=100.0,
            momentum=0.05,
            z_trend=0.1,
            ma_crosses_recent=0.0,
            router=r,
            momentum_config=m,
        )
        == "ranging"
    )


def test_classify_regime_ranging_many_crosses():
    r = _router()
    m = _mom_cfg()
    assert (
        classify_regime_raw(
            fast_ma=110.0,
            slow_ma=100.0,
            momentum=0.05,
            z_trend=0.8,
            ma_crosses_recent=3.0,
            router=r,
            momentum_config=m,
        )
        == "ranging"
    )


def test_model_for_regime_mapping():
    r = _router()
    assert model_for_regime("trending", r) == "momentum_benchmark"
    assert model_for_regime("ranging", r) == "perturbation"
    assert model_for_regime("uncertain", r) == "perturbation"


def test_apply_regime_hysteresis_delays_switch():
    raw = pd.Series(["trending"] * 5 + ["ranging"] * 5)
    stable = apply_regime_hysteresis(raw, min_days=3)
    assert list(stable.iloc[:7]) == ["trending"] * 7
    assert list(stable.iloc[7:]) == ["ranging"] * 3


def test_count_ma_crossovers():
    idx = pd.date_range("2024-01-01", periods=6, freq="D", tz="UTC")
    fast = pd.Series([90, 110, 105, 95, 105, 95], index=idx, dtype=float)
    slow = pd.Series([100, 100, 100, 100, 100, 100], index=idx, dtype=float)
    crosses = count_ma_crossovers(fast, slow, lookback=5)
    assert float(crosses.iloc[-1]) >= 2.0


def test_regime_at_index_unchanged_when_future_appended():
    idx = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
    fast = pd.Series(np.linspace(90, 120, 10), index=idx)
    slow = pd.Series(100.0, index=idx)
    crosses_full = count_ma_crossovers(fast, slow, 5)
    crosses_head = count_ma_crossovers(fast.iloc[:5], slow.iloc[:5], 5)
    assert float(crosses_full.iloc[4]) == float(crosses_head.iloc[4])
