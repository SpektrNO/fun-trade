import pandas as pd

from funtrade.portfolio.overlay import (
    build_portfolio_overlay,
    overlay_action_for_holding,
)


def test_overlay_add_when_top_rank():
    action = overlay_action_for_holding(
        epsilon=-2.0,
        threshold=0.85,
        regime_valid=True,
        add_rank=1,
    )
    assert action.label == "Add (dip)"
    assert "#1" in action.detail


def test_overlay_hold_when_not_ranked():
    action = overlay_action_for_holding(
        epsilon=-2.0,
        threshold=0.85,
        regime_valid=True,
    )
    assert action.label == "Hold"


def test_build_portfolio_overlay_keeps_top_dips_only():
    rec = pd.DataFrame(
        [
            {"symbol": f"S{i}", "epsilon": -2.0 + i * 0.05, "threshold": 0.85, "regime_valid": True, "in_portfolio": True}
            for i in range(5)
        ]
    )
    overlay = build_portfolio_overlay(rec, max_adds=2, max_trims=0)
    assert len(overlay) == 2
    assert set(overlay["overlay_action"]) == {"Add (dip)"}
    assert overlay.iloc[0]["symbol"] == "S0"


def test_build_portfolio_overlay_ignores_non_portfolio_symbols():
    rec = pd.DataFrame(
        [
            {"symbol": "HOLD", "epsilon": -2.0, "threshold": 0.85, "regime_valid": True, "in_portfolio": True},
            {"symbol": "WATCH", "epsilon": -3.0, "threshold": 0.85, "regime_valid": True, "in_portfolio": False},
        ]
    )
    overlay = build_portfolio_overlay(rec, max_adds=1, max_trims=0)
    assert len(overlay) == 1
    assert overlay.iloc[0]["symbol"] == "HOLD"
