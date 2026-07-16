import pandas as pd

from funtrade.ui.plotting.data import (
    build_momentum_price_overlay,
    build_rsi_chart_frame,
    prepare_trade_chart_frames,
    price_chart_series,
    rsi_chart_series,
    rsi_threshold_levels,
)


def test_build_momentum_price_overlay_includes_bollinger_bands():
    idx = pd.date_range("2024-01-01", periods=250, freq="D", tz="UTC")
    price = pd.Series(range(100, 350), index=idx, dtype=float)
    fast = price.rolling(50, min_periods=10).mean()
    slow = price.rolling(200, min_periods=20).mean()
    mom = pd.DataFrame({"price": price, "fast_ma": fast, "slow_ma": slow}, index=idx).dropna()

    overlay = build_momentum_price_overlay(mom, slow_ma_days=200)
    assert not overlay.empty
    assert "Fast MA" in overlay.columns
    assert "Upper band (+2σ)" in overlay.columns
    assert "Lower band (−2σ)" in overlay.columns
    valid = overlay.dropna()
    assert (valid["Upper band (+2σ)"] >= valid["Slow MA"]).all()
    assert (valid["Lower band (−2σ)"] <= valid["Slow MA"]).all()


def test_prepare_trade_chart_frames_merges_momentum_overlay():
    idx = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
    series = pd.DataFrame(
        {"epsilon": [0.0] * 5, "regime_valid": True, "price": [100.0, 101, 102, 103, 104]},
        index=idx,
    )
    overlay = pd.DataFrame(
        {
            "time": pd.to_datetime(idx),
            "Fast MA": [99.0, 99.5, 100.0, 100.5, 101.0],
            "Slow MA": [98.0, 98.2, 98.4, 98.6, 98.8],
            "Upper band (+2σ)": [102.0, 102.1, 102.2, 102.3, 102.4],
            "Lower band (−2σ)": [96.0, 96.1, 96.2, 96.3, 96.4],
        }
    )
    charts = prepare_trade_chart_frames(
        series, epsilon_threshold=0.75, momentum_overlay=overlay,
    )
    cols = price_chart_series(charts["price"])
    assert cols == ["price", "Fast MA", "Slow MA", "Upper band (+2σ)", "Lower band (−2σ)"]


def test_build_rsi_chart_frame_mean_reversion_thresholds():
    idx = pd.date_range("2024-01-01", periods=30, freq="D", tz="UTC")
    mom = pd.DataFrame({"rsi": [25.0 + i * 1.5 for i in range(30)]}, index=idx)
    frame = build_rsi_chart_frame(
        mom,
        rsi_mode="mean_reversion",
        rsi_buy_min=50.0,
        rsi_sell_max=50.0,
        rsi_oversold=30.0,
        rsi_overbought=70.0,
    )
    assert list(frame.columns) == ["time", "RSI", "Buy < 30", "Sell > 70"]
    assert frame["Buy < 30"].iloc[-1] == 30.0
    assert rsi_chart_series(frame) == ["RSI", "Buy < 30", "Sell > 70"]
    buy, sell, buy_lbl, sell_lbl = rsi_threshold_levels(
        {"rsi_mode": "mean_reversion", "rsi_oversold": 30.0, "rsi_overbought": 70.0}
    )
    assert buy == 30.0 and sell == 70.0
    assert buy_lbl == "Buy < 30" and sell_lbl == "Sell > 70"
