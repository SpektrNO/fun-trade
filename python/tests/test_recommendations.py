import pandas as pd

from funtrade.models.perturbation import signal_from_epsilon
from funtrade.ui.service import _recommendation_note


def test_recommendation_signal_buy():
    sig = signal_from_epsilon(-1.0, 0.75, True, long_only=True, current_position=0.0)
    assert sig == 1
    note = _recommendation_note(
        epsilon=-1.0,
        threshold=0.75,
        regime_valid=True,
        signal=sig,
        position_shares=0.0,
        z_trend=0.0,
        trend_gate_sells=False,
        trend_gate_z=0.5,
    )
    assert note == "Mean-reversion buy"


def test_recommendation_signal_blocked_by_regime():
    sig = signal_from_epsilon(-1.0, 0.75, False, long_only=True, current_position=0.0)
    assert sig == 0
    note = _recommendation_note(
        epsilon=-1.0,
        threshold=0.75,
        regime_valid=False,
        signal=sig,
        position_shares=0.0,
        z_trend=0.0,
        trend_gate_sells=False,
        trend_gate_z=0.5,
    )
    assert note == "Buy blocked (regime)"


def test_load_latest_perturbation_snapshots(monkeypatch):
    from funtrade.data import loader

    df = pd.DataFrame(
        [
            {
                "time": pd.Timestamp("2026-07-10", tz="UTC"),
                "symbol": "VWCE.DE",
                "asset_class": "etf",
                "epsilon": -0.9,
                "magnitude": 0.9,
                "regime_valid": True,
                "z_return": -1.0,
                "z_volume": 0.1,
                "z_rel_strength": -0.2,
                "price": 100.0,
                "computed_at": pd.Timestamp("2026-07-11 12:00:00", tz="UTC"),
            }
        ]
    )

    monkeypatch.setattr(loader, "read_sql_df", lambda *a, **k: df)

    snaps = loader.load_latest_perturbation_snapshots(["VWCE.DE"])
    assert "VWCE.DE" in snaps
    assert snaps["VWCE.DE"].epsilon == -0.9
    assert snaps["VWCE.DE"].regime_valid is True
