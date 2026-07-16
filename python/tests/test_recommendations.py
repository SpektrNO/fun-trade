import pandas as pd
import pytest
from dataclasses import replace

from funtrade.models.perturbation import signal_from_epsilon
from funtrade.ui.service import _recommendation_note, _recommendation_position_qty, _sort_recommendations_by_position, default_ui_params, format_position_shares, params_draft_pending


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


def test_recommendation_position_qty_uses_portfolio_value():
    qty, assumed = _recommendation_position_qty(
        symbol="VWCE.DE",
        paper_qty=0.0,
        assume_holding_all=False,
        held_symbols=frozenset({"VWCE.DE"}),
        assumed_eur=1000.0,
        price=100.0,
        portfolio_value=50000.0,
    )
    assert qty == 500.0
    assert assumed is False


def test_recommendation_position_qty_prefers_portfolio_over_paper_dust():
    qty, assumed = _recommendation_position_qty(
        symbol="FONDSFINANS.UTBYTTE.B",
        paper_qty=0.11,
        assume_holding_all=False,
        held_symbols=frozenset({"FONDSFINANS.UTBYTTE.B"}),
        assumed_eur=1000.0,
        price=32063.85,
        portfolio_value=82363.0,
    )
    assert qty == pytest.approx(82363.0 / 32063.85)
    assert assumed is False


def test_format_position_shares_avoids_zero_rounding():
    assert format_position_shares(0.11) == "0.11"
    assert format_position_shares(2.57) == "2.6"
    assert format_position_shares(89.7) == "89.7"
    assert format_position_shares(150.0) == "150"
    assert format_position_shares(0.0) == "0"
    assert format_position_shares(10.0, assumed=True) == "10.0*"


def test_sort_recommendations_by_position():
    df = pd.DataFrame(
        [
            {"symbol": "A", "position_shares": 0.0, "in_portfolio": False},
            {"symbol": "B", "position_shares": 100.0, "in_portfolio": True},
            {"symbol": "C", "position_shares": 50.0, "in_portfolio": True},
        ]
    )
    sorted_df = _sort_recommendations_by_position(df)
    assert list(sorted_df["symbol"]) == ["B", "C", "A"]


def test_params_draft_pending_detects_sidebar_changes():
    base = default_ui_params("VWCE.DE")
    changed = replace(base, epsilon_threshold=base.epsilon_threshold + 0.1)
    assert not params_draft_pending(base, base)
    assert params_draft_pending(base, changed)


def test_resolve_recommendation_scope_with_portfolio(tmp_path, monkeypatch):
    import json

    from funtrade.ui.service import resolve_recommendation_scope

    monkeypatch.setattr("funtrade.portfolio_config.repo_root", lambda: tmp_path)
    path = tmp_path / "portfolio_private.json"
    path.write_text(
        json.dumps(
            {
                "name": "Private",
                "valuation_mode": "weight_pct",
                "holdings": [
                    {"symbol": "VWCE.DE", "weight_pct": 60.0},
                    {"symbol": "AGGH.DE", "weight_pct": 40.0},
                ],
            }
        ),
        encoding="utf-8",
    )
    scoped = resolve_recommendation_scope(
        path, watchlist=["VWCE.DE", "AGGH.DE", "EUNL.DE"],
    )
    assert scoped.symbols is None
    assert scoped.held_symbols == frozenset({"VWCE.DE", "AGGH.DE"})
    assert scoped.portfolio_weights == {"VWCE.DE": 60.0, "AGGH.DE": 40.0}
    assert scoped.portfolio_name == "Private"


def test_auto_recommendations_use_momentum_when_trending(monkeypatch):
    from funtrade.ui import service as svc

    pert = pd.DataFrame(
        [
            {
                "symbol": "VWCE.DE",
                "asset_class": "etf",
                "as_of": "2026-07-10",
                "price": 100.0,
                "epsilon": -0.5,
                "threshold": 0.75,
                "regime_valid": True,
                "z_trend": 0.8,
                "market_regime": "trending",
                "selected_model": "momentum_benchmark",
                "position_shares": 0.0,
                "position_assumed": False,
                "signal": 1,
                "action": "BUY",
                "note": "Mean-reversion buy",
            }
        ]
    )
    pert.attrs["model"] = svc.MODEL_PERTURBATION
    mom = pd.DataFrame(
        [
            {
                "symbol": "VWCE.DE",
                "asset_class": "etf",
                "as_of": "2026-07-10",
                "price": 100.0,
                "fast_ma": 110.0,
                "slow_ma": 100.0,
                "rsi": 62.0,
                "momentum_pct": 5.0,
                "rsi_bullish": True,
                "ma_bullish": True,
                "position_shares": 0.0,
                "position_assumed": False,
                "signal": 0,
                "action": "HOLD",
                "note": "Already long",
            }
        ]
    )
    mom.attrs["model"] = svc.MODEL_MOMENTUM_BENCHMARK

    monkeypatch.setattr(svc, "_fetch_perturbation_recommendations", lambda *a, **k: pert)
    monkeypatch.setattr(svc, "_fetch_momentum_recommendations", lambda *a, **k: mom)

    from funtrade.ui.service import default_ui_params

    df = svc.fetch_recommendations(default_ui_params(), model=svc.MODEL_AUTO)
    assert df.iloc[0]["selected_model"] == svc.MODEL_MOMENTUM_BENCHMARK
    assert df.iloc[0]["action"] == "HOLD"
    assert df.attrs["model"] == svc.MODEL_AUTO


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
