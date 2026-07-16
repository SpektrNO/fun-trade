import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from funtrade.models.momentum import (
    compute_rsi,
    momentum_backtest_signal,
    momentum_trade_qty,
    signal_from_momentum,
)
from funtrade.universe_config import MomentumBenchmarkConfig


def _cfg(**kwargs) -> MomentumBenchmarkConfig:
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
        require_momentum_for_buy=False,
        exit_on_rsi_weak=True,
        position_mode="scale",
    )
    defaults.update(kwargs)
    return MomentumBenchmarkConfig(**defaults)


def test_compute_rsi_rising_series_is_high():
    prices = pd.Series(np.linspace(100, 130, 40))
    rsi = compute_rsi(prices, period=14)
    assert rsi.iloc[-1] > 70


def test_signal_buy_on_bullish_rsi():
    sig = signal_from_momentum(
        rsi=62.0,
        momentum=0.05,
        current_position=0.0,
        config=_cfg(),
    )
    assert sig == 1


def test_signal_hold_when_already_long():
    sig = signal_from_momentum(
        rsi=62.0,
        momentum=0.05,
        current_position=50.0,
        config=_cfg(position_mode="slice"),
    )
    assert sig == 0


def test_signal_sell_on_weak_rsi_when_long():
    sig = signal_from_momentum(
        rsi=42.0,
        momentum=-0.02,
        current_position=50.0,
        config=_cfg(),
    )
    assert sig == -1


def test_signal_blocked_by_weak_return_filter():
    sig = signal_from_momentum(
        rsi=62.0,
        momentum=-0.01,
        current_position=0.0,
        config=_cfg(require_momentum_for_buy=True, momentum_threshold=0.0),
    )
    assert sig == 0


def test_mean_reversion_buy_oversold():
    cfg = _cfg(rsi_mode="mean_reversion")
    assert signal_from_momentum(rsi=25.0, momentum=0.0, current_position=0.0, config=cfg) == 1
    assert signal_from_momentum(rsi=35.0, momentum=0.0, current_position=0.0, config=cfg) == 0


def test_mean_reversion_sell_overbought_when_long():
    cfg = _cfg(rsi_mode="mean_reversion")
    assert signal_from_momentum(rsi=75.0, momentum=0.0, current_position=50.0, config=cfg) == -1
    assert signal_from_momentum(rsi=75.0, momentum=0.0, current_position=0.0, config=cfg) == 0


def test_mean_reversion_scale_adds_while_oversold():
    cfg = _cfg(rsi_mode="mean_reversion", position_mode="scale")
    assert momentum_backtest_signal(rsi=25.0, momentum=0.0, current_position=50.0, config=cfg) == 1
    assert momentum_backtest_signal(rsi=75.0, momentum=0.0, current_position=50.0, config=cfg) == -1


def test_scale_signal_adds_each_bullish_day():
    cfg = _cfg(position_mode="scale")
    assert (
        momentum_backtest_signal(
            rsi=62.0,
            momentum=0.05,
            current_position=50.0,
            config=cfg,
        )
        == 1
    )
    assert (
        momentum_backtest_signal(
            rsi=42.0,
            momentum=-0.02,
            current_position=50.0,
            config=cfg,
        )
        == -1
    )


def test_scale_trade_qty_uses_paper_slice():
    from funtrade.execution.paper import PaperSettings

    paper = PaperSettings(
        initial_cash=10_000,
        trade_slice_pct=0.10,
        position_limit_shares=500,
        fee_bps=0,
        csv_path=Path("data/paper_trades.csv"),
    )
    cfg = _cfg(position_mode="scale")
    buy_qty = momentum_trade_qty(
        side="buy",
        price=100.0,
        cash_eur=10_000,
        net_qty=0.0,
        paper=paper,
        config=cfg,
    )
    assert buy_qty == pytest.approx(10.0)  # 10% of 10k / 100

    sell_qty = momentum_trade_qty(
        side="sell",
        price=100.0,
        cash_eur=5_000,
        net_qty=50.0,
        paper=paper,
        config=cfg,
    )
    assert sell_qty == pytest.approx(10.0)  # 10% slice in shares, not full exit


def test_momentum_recommendation_note_scale_add_slice():
    from funtrade.ui.service import _momentum_recommendation_note

    note = _momentum_recommendation_note(
        signal=1,
        price=100.0,
        rsi=62.0,
        rsi_bullish=True,
        momentum=0.05,
        config=_cfg(position_mode="scale"),
        position_shares=50.0,
    )
    assert "add slice" in note
    assert "RSI" in note


def test_run_momentum_backtest_trades(monkeypatch):
    import funtrade.backtest.engine as eng

    idx = pd.date_range("2020-01-01", periods=400, freq="D", tz="UTC")
    test_idx = idx[280:]

    prices = np.concatenate([np.linspace(80, 90, 280), np.linspace(90, 120, 120)])
    price_df = pd.DataFrame({"price": prices, "volume": 1e6}, index=idx)

    def fake_load(symbol, market="adj_close", **kwargs):
        return price_df

    def fake_series(symbol, **kwargs):
        from funtrade.models.momentum import momentum_frame_from_prices

        return momentum_frame_from_prices(price_df["price"].astype(float), _cfg(
            fast_ma_days=20,
            slow_ma_days=60,
            momentum_lookback_days=10,
        ))

    monkeypatch.setattr(eng, "load_price_bars", fake_load)
    monkeypatch.setattr(eng, "compute_momentum_series", fake_series)

    cfg = _cfg(
        fast_ma_days=20,
        slow_ma_days=60,
        momentum_lookback_days=10,
        position_mode="scale",
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
