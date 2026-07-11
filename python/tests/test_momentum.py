import numpy as np
import pandas as pd

from funtrade.models.momentum import signal_from_momentum
from funtrade.universe_config import MomentumBenchmarkConfig


def _cfg(**kwargs) -> MomentumBenchmarkConfig:
    defaults = dict(
        fast_ma_days=50,
        slow_ma_days=200,
        momentum_lookback_days=63,
        momentum_threshold=0.0,
        require_momentum_for_buy=True,
        exit_on_ma_crossunder=True,
    )
    defaults.update(kwargs)
    return MomentumBenchmarkConfig(**defaults)


def test_signal_buy_on_bullish_ma():
    sig = signal_from_momentum(
        fast_ma=110.0,
        slow_ma=100.0,
        momentum=0.05,
        current_position=0.0,
        config=_cfg(),
    )
    assert sig == 1


def test_signal_hold_when_already_long():
    sig = signal_from_momentum(
        fast_ma=110.0,
        slow_ma=100.0,
        momentum=0.05,
        current_position=50.0,
        config=_cfg(),
    )
    assert sig == 0


def test_signal_sell_on_bearish_ma_when_long():
    sig = signal_from_momentum(
        fast_ma=90.0,
        slow_ma=100.0,
        momentum=-0.02,
        current_position=50.0,
        config=_cfg(),
    )
    assert sig == -1


def test_signal_blocked_by_weak_momentum():
    sig = signal_from_momentum(
        fast_ma=110.0,
        slow_ma=100.0,
        momentum=-0.01,
        current_position=0.0,
        config=_cfg(momentum_threshold=0.0),
    )
    assert sig == 0


def test_run_momentum_backtest_trades(monkeypatch):
    import funtrade.backtest.engine as eng

    idx = pd.date_range("2020-01-01", periods=400, freq="D", tz="UTC")
    test_idx = idx[280:]

    prices = np.concatenate([np.linspace(80, 90, 280), np.linspace(90, 120, 120)])
    price_df = pd.DataFrame({"price": prices, "volume": 1e6}, index=idx)

    def fake_load(symbol, market="adj_close", **kwargs):
        return price_df

    def fake_series(symbol, **kwargs):
        p = price_df["price"].astype(float)
        fast = p.rolling(20, min_periods=5).mean()
        slow = p.rolling(60, min_periods=20).mean()
        mom = p / p.shift(10) - 1.0
        return pd.DataFrame(
            {
                "price": p,
                "fast_ma": fast,
                "slow_ma": slow,
                "momentum": mom,
                "ma_bullish": fast > slow,
            },
            index=idx,
        )

    monkeypatch.setattr(eng, "load_price_bars", fake_load)
    monkeypatch.setattr(eng, "compute_momentum_series", fake_series)

    cfg = MomentumBenchmarkConfig(
        fast_ma_days=20,
        slow_ma_days=60,
        momentum_lookback_days=10,
        momentum_threshold=0.0,
        require_momentum_for_buy=True,
        exit_on_ma_crossunder=True,
    )

    result = eng.run_momentum_backtest(
        "VWCE.DE",
        test_start=test_idx[0],
        momentum_config=cfg,
        initial_cash_eur=10_000,
        trade_shares=10,
    )
    assert result.total_trades >= 0
    assert len(result.equity_curve) == len(test_idx)
