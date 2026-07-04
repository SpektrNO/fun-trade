import numpy as np
import pandas as pd

from funtrade.backtest.engine import _compute_metrics, run_backtest


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
            return pd.DataFrame({"residual": np.zeros(len(p))}, index=p.index)

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
            return pd.DataFrame({"residual": np.zeros(len(p))}, index=p.index)

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
            return pd.DataFrame({"residual": np.zeros(len(p))}, index=p.index)

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
