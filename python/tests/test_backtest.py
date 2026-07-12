import numpy as np
import pandas as pd
import pytest

from funtrade.backtest.engine import _compute_metrics, buy_and_hold_from_prices, run_backtest


def test_compute_metrics_additive_pnl():
    pnl = pd.Series([1.0, -0.5, 2.0, 0.0])
    trades = pd.Series([1.0, 0.0, 1.0, 0.0])
    m = _compute_metrics(pnl, trades)
    assert m["total_return"] == 2.5
    assert m["total_trades"] >= 1
    assert 0 <= m["hit_rate"] <= 1


def test_compute_metrics_empty():
    m = _compute_metrics(pd.Series(dtype=float), pd.Series(dtype=float))
    assert m["total_return"] == 0.0
    assert m["total_trades"] == 0


def test_run_backtest_tracks_position(monkeypatch):
    import funtrade.backtest.engine as eng
    import funtrade.data.loader as loader

    idx = pd.date_range("2024-01-01", periods=200, freq="D", tz="UTC")
    train_end = idx[139]
    test_idx = idx[140:]

    price_df = pd.DataFrame({"price": np.linspace(40, 60, len(idx)), "volume": 1e6}, index=idx)

    def fake_load(symbol, market="adj_close", **kwargs):
        return price_df

    class FakeEq:
        symbol = "VWCE.DE"
        sigma = 1.0
        half_life_days = 7.0

        def equilibrium_band(self, prices, *, symbol=None, **kwargs):
            p = prices.astype(float)
            return pd.DataFrame({"residual": np.zeros(len(p)), "equilibrium": p.values}, index=p.index)

    def fake_series(symbol, **kwargs):
        eps = pd.Series(0.0, index=idx)
        eps.iloc[145] = -3.0
        eps.iloc[146] = -3.0
        rv = pd.Series(True, index=idx)
        return pd.DataFrame(
            {
                "epsilon": eps,
                "magnitude": eps.abs(),
                "z_return": eps,
                "z_volume": 0.0,
                "z_rel_strength": 0.0,
                "z_vol": 0.0,
                "regime_valid": rv,
                "price": price_df["price"],
            },
            index=idx,
        )

    monkeypatch.setattr(eng, "load_price_bars", fake_load)
    monkeypatch.setattr(eng, "calibrate_equilibrium", lambda *a, **k: FakeEq())
    monkeypatch.setattr(eng, "compute_perturbation_series", fake_series)

    result = run_backtest(
        "VWCE.DE",
        train_end=train_end,
        test_start=test_idx[0],
        epsilon_threshold=2.0,
        persist=False,
    )
    assert result.total_trades >= 1
    assert result.position_shares.loc[test_idx].max() > 0


def test_backtest_realized_vs_unrealized(monkeypatch):
    import funtrade.backtest.engine as eng

    idx = pd.date_range("2024-01-01", periods=200, freq="D", tz="UTC")
    train_end = idx[139]
    test_idx = idx[140:]

    prices = np.linspace(40, 60, len(idx))
    price_df = pd.DataFrame({"price": prices, "volume": 1e6}, index=idx)

    def fake_load(symbol, market="adj_close", **kwargs):
        return price_df

    class FakeEq:
        symbol = "VWCE.DE"
        sigma = 1.0
        half_life_days = 7.0

        def equilibrium_band(self, prices, *, symbol=None, **kwargs):
            p = prices.astype(float)
            return pd.DataFrame({"residual": np.zeros(len(p)), "equilibrium": p.values}, index=p.index)

    def fake_series(symbol, **kwargs):
        eps = pd.Series(0.0, index=idx)
        eps.iloc[145] = -3.0
        eps.iloc[150] = 3.0
        rv = pd.Series(True, index=idx)
        return pd.DataFrame(
            {
                "epsilon": eps,
                "magnitude": eps.abs(),
                "z_return": eps,
                "z_volume": 0.0,
                "z_rel_strength": 0.0,
                "z_vol": 0.0,
                "regime_valid": rv,
                "price": price_df["price"],
            },
            index=idx,
        )

    monkeypatch.setattr(eng, "load_price_bars", fake_load)
    monkeypatch.setattr(eng, "calibrate_equilibrium", lambda *a, **k: FakeEq())
    monkeypatch.setattr(eng, "compute_perturbation_series", fake_series)

    result = run_backtest(
        "VWCE.DE",
        train_end=train_end,
        test_start=test_idx[0],
        epsilon_threshold=2.0,
        initial_cash_eur=10_000,
        trade_shares=10,
        persist=False,
    )
    m = result.metrics
    assert m["realized_pnl_eur"] > 0
    assert m["unrealized_pnl_eur"] == 0.0
    assert m["final_shares"] == 0.0
    assert abs(m["net_profit_eur"] - (m["total_pnl_eur"] - m["total_fees_eur"])) < 1e-6


def test_run_backtest_saved_h0_uses_load_or_calibrate(monkeypatch):
    import funtrade.backtest.engine as eng

    idx = pd.date_range("2024-01-01", periods=200, freq="D", tz="UTC")
    price_df = pd.DataFrame({"price": np.linspace(40, 60, len(idx)), "volume": 1e6}, index=idx)
    load_calls = {"calibrate": 0, "load": 0}

    class FakeEq:
        symbol = "VWCE.DE"
        sigma = 1.0
        kappa = 0.1
        mu = 0.0
        half_life_days = 7.0

        def equilibrium_band(self, prices, **kwargs):
            p = prices.astype(float)
            return pd.DataFrame({"residual": np.zeros(len(p)), "equilibrium": p.values}, index=p.index)

    def fake_load(symbol, market="adj_close", **kwargs):
        return price_df

    def fake_calibrate(*args, **kwargs):
        load_calls["calibrate"] += 1
        return FakeEq()

    def fake_load_or_calibrate(*args, **kwargs):
        load_calls["load"] += 1
        return FakeEq()

    def fake_series(symbol, **kwargs):
        eps = pd.Series(0.0, index=idx)
        rv = pd.Series(True, index=idx)
        return pd.DataFrame(
            {
                "epsilon": eps,
                "magnitude": eps.abs(),
                "z_return": eps,
                "z_volume": 0.0,
                "z_rel_strength": 0.0,
                "z_vol": 0.0,
                "regime_valid": rv,
                "price": price_df["price"],
            },
            index=idx,
        )

    monkeypatch.setattr(eng, "load_price_bars", fake_load)
    monkeypatch.setattr(eng, "calibrate_equilibrium", fake_calibrate)
    monkeypatch.setattr(eng, "load_or_calibrate", fake_load_or_calibrate)
    monkeypatch.setattr(eng, "compute_perturbation_series", fake_series)

    run_backtest("VWCE.DE", h0_source=eng.H0_SOURCE_SAVED, persist=False)
    assert load_calls["load"] == 1
    assert load_calls["calibrate"] == 0

    load_calls["load"] = 0
    run_backtest("VWCE.DE", h0_source=eng.H0_SOURCE_WALK_FORWARD, persist=False)
    assert load_calls["calibrate"] == 1
    assert load_calls["load"] == 0


def test_buy_and_hold_from_prices():
    prices = pd.Series([100.0, 110.0, 121.0])
    bh = buy_and_hold_from_prices(prices, 100_000.0)
    assert bh["profit_eur"] == pytest.approx(21_000.0)
    assert bh["return_pct"] == pytest.approx(21.0)


def test_buy_and_hold_independent_of_strategy_trades(monkeypatch):
    import funtrade.backtest.engine as eng

    idx = pd.date_range("2024-01-01", periods=200, freq="D", tz="UTC")
    train_end = idx[139]
    test_idx = idx[140:]
    price_df = pd.DataFrame({"price": np.linspace(100.0, 150.0, len(idx)), "volume": 1e6}, index=idx)

    def fake_load(symbol, market="adj_close", **kwargs):
        return price_df

    class FakeEq:
        symbol = "VWCE.DE"
        sigma = 1.0
        half_life_days = 7.0

        def equilibrium_band(self, prices, *, symbol=None, **kwargs):
            p = prices.astype(float)
            return pd.DataFrame({"residual": np.zeros(len(p)), "equilibrium": p.values}, index=p.index)

    def fake_series(symbol, **kwargs):
        eps = pd.Series(0.0, index=idx)
        rv = pd.Series(True, index=idx)
        return pd.DataFrame(
            {
                "epsilon": eps,
                "magnitude": eps.abs(),
                "z_return": eps,
                "z_volume": 0.0,
                "z_rel_strength": 0.0,
                "z_vol": 0.0,
                "regime_valid": rv,
                "price": price_df["price"],
            },
            index=idx,
        )

    monkeypatch.setattr(eng, "load_price_bars", fake_load)
    monkeypatch.setattr(eng, "calibrate_equilibrium", lambda *a, **k: FakeEq())
    monkeypatch.setattr(eng, "compute_perturbation_series", fake_series)

    result = run_backtest(
        "VWCE.DE",
        train_end=train_end,
        test_start=test_idx[0],
        epsilon_threshold=2.0,
        persist=False,
    )
    assert result.total_trades == 0
    assert result.metrics["net_profit_eur"] == pytest.approx(0.0)
    assert result.metrics["buy_and_hold_profit_eur"] > 0.0


def test_backtest_uses_slice_sizing_by_default(monkeypatch):
    import funtrade.backtest.engine as eng

    idx = pd.date_range("2024-01-01", periods=200, freq="D", tz="UTC")
    train_end = idx[139]
    test_idx = idx[140:]
    price_df = pd.DataFrame({"price": np.full(len(idx), 100.0), "volume": 1e6}, index=idx)

    def fake_load(symbol, market="adj_close", **kwargs):
        return price_df

    class FakeEq:
        symbol = "VWCE.DE"
        sigma = 1.0
        half_life_days = 7.0

        def equilibrium_band(self, prices, *, symbol=None, **kwargs):
            p = prices.astype(float)
            residual = np.zeros(len(p))
            if len(p) > 5:
                residual[5] = -3.0
                residual[6] = -3.0
            return pd.DataFrame({"residual": residual, "equilibrium": p.values}, index=p.index)

    def fake_series(symbol, **kwargs):
        eps = pd.Series(0.0, index=idx)
        eps.iloc[145] = -3.0
        eps.iloc[146] = -3.0
        rv = pd.Series(True, index=idx)
        return pd.DataFrame(
            {
                "epsilon": eps,
                "magnitude": eps.abs(),
                "z_return": eps,
                "z_volume": 0.0,
                "z_rel_strength": 0.0,
                "z_vol": 0.0,
                "regime_valid": rv,
                "price": price_df["price"],
            },
            index=idx,
        )

    monkeypatch.setattr(eng, "load_price_bars", fake_load)
    monkeypatch.setattr(eng, "calibrate_equilibrium", lambda *a, **k: FakeEq())
    monkeypatch.setattr(eng, "compute_perturbation_series", fake_series)

    result = run_backtest(
        "VWCE.DE",
        train_end=train_end,
        test_start=test_idx[0],
        epsilon_threshold=2.0,
        initial_cash_eur=100_000.0,
        trade_slice_pct=0.10,
        persist=False,
    )
    assert result.total_trades >= 1
    bought = float(result.trade_volume_shares[result.trade_volume_shares > 0].iloc[0])
    assert abs(bought - 66.66666666666666) < 0.01  # €10k slice scaled by |ε|/threshold @ €100


def test_run_backtest_passes_regime_settings_to_perturbation(monkeypatch):
    import funtrade.backtest.engine as eng
    from dataclasses import replace
    from funtrade.config import Settings

    idx = pd.date_range("2024-01-01", periods=200, freq="D", tz="UTC")
    train_end = idx[139]
    test_idx = idx[140:]
    price_df = pd.DataFrame({"price": np.full(len(idx), 100.0), "volume": 1e6}, index=idx)
    captured: list = []

    def fake_load(symbol, market="adj_close", **kwargs):
        return price_df

    class FakeEq:
        symbol = "VWCE.DE"
        sigma = 1.0
        half_life_days = 7.0

        def equilibrium_band(self, prices, *, symbol=None, **kwargs):
            p = prices.astype(float)
            return pd.DataFrame({"residual": np.zeros(len(p)), "equilibrium": p.values}, index=p.index)

    def fake_series(symbol, *, settings=None, **kwargs):
        captured.append(settings)
        eps = pd.Series(0.0, index=idx)
        return pd.DataFrame(
            {
                "epsilon": eps,
                "magnitude": eps.abs(),
                "z_return": eps,
                "z_volume": 0.0,
                "z_rel_strength": 0.0,
                "z_vol": 0.0,
                "regime_valid": pd.Series(True, index=idx),
                "price": price_df["price"],
            },
            index=idx,
        )

    monkeypatch.setattr(eng, "load_price_bars", fake_load)
    monkeypatch.setattr(eng, "calibrate_equilibrium", lambda *a, **k: FakeEq())
    monkeypatch.setattr(eng, "compute_perturbation_series", fake_series)

    settings = replace(
        Settings.from_env().for_symbol("VWCE.DE"),
        regime_spike_sigma=4.5,
        regime_consecutive_bars=7,
    )
    run_backtest(
        "VWCE.DE",
        train_end=train_end,
        test_start=test_idx[0],
        settings=settings,
        persist=False,
    )
    assert captured
    assert captured[0].regime_spike_sigma == 4.5
    assert captured[0].regime_consecutive_bars == 7


def test_run_mixed_backtest_smoke(monkeypatch):
    import funtrade.backtest.engine as eng

    idx = pd.date_range("2020-01-01", periods=400, freq="D", tz="UTC")
    test_idx = idx[280:]
    prices = np.concatenate([np.linspace(80, 90, 280), np.linspace(90, 120, 120)])
    price_df = pd.DataFrame({"price": prices, "volume": 1e6}, index=idx)

    def fake_load(symbol, market="adj_close", **kwargs):
        return price_df

    def fake_pert(symbol, **kwargs):
        p = price_df["price"].astype(float)
        return pd.DataFrame(
            {
                "epsilon": np.zeros(len(p)),
                "magnitude": np.zeros(len(p)),
                "z_return": np.zeros(len(p)),
                "z_volume": np.zeros(len(p)),
                "z_rel_strength": np.zeros(len(p)),
                "z_vol": np.zeros(len(p)),
                "z_trend": np.zeros(len(p)),
                "regime_valid": True,
                "price": p,
            },
            index=idx,
        )

    def fake_mom(symbol, **kwargs):
        p = price_df["price"].astype(float)
        fast = p.rolling(20, min_periods=5).mean()
        slow = p.rolling(60, min_periods=20).mean()
        mom = p / p.shift(10) - 1.0
        return pd.DataFrame(
            {"price": p, "fast_ma": fast, "slow_ma": slow, "momentum": mom, "ma_bullish": fast > slow},
            index=idx,
        )

    def fake_regime(symbol, **kwargs):
        pert = fake_pert(symbol)
        return pd.DataFrame(
            {
                "market_regime_raw": "trending",
                "market_regime": "trending",
                "selected_model": "momentum_benchmark",
            },
            index=pert.index,
        )

    class FakeEq:
        symbol = "VWCE.DE"
        sigma = 0.05
        half_life_days = 30.0
        mu = 0.0

        def deseasonalize(self, p):
            return np.log(p.astype(float))

        def seasonal_component(self, index):
            return np.zeros(len(index))

        def equilibrium_band(self, prices, **kwargs):
            p = prices.astype(float)
            return pd.DataFrame({"residual": np.zeros(len(p)), "equilibrium": p.values}, index=p.index)

    monkeypatch.setattr(eng, "load_price_bars", fake_load)
    monkeypatch.setattr(eng, "compute_perturbation_series", fake_pert)
    monkeypatch.setattr(eng, "compute_momentum_series", fake_mom)
    monkeypatch.setattr(eng, "compute_regime_series", fake_regime)
    monkeypatch.setattr(eng, "resolve_h0_equilibrium", lambda *a, **k: FakeEq())

    result = eng.run_mixed_backtest(
        "VWCE.DE",
        test_start=test_idx[0],
        initial_cash_eur=10_000,
    )
    assert len(result.equity_curve) == len(test_idx)
    assert result.total_trades >= 0

