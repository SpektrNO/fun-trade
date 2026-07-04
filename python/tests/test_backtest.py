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

        def equilibrium_band(self, prices, *, symbol=None):
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
