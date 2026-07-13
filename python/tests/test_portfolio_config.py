import json

import pytest

from funtrade.portfolio_config import load_portfolio_config, reset_portfolio_config_cache


def test_load_portfolio_config_missing_returns_none(tmp_path, monkeypatch):
    reset_portfolio_config_cache()
    missing = tmp_path / "portfolio.json"
    monkeypatch.setenv("FUNTRADE_PORTFOLIO", str(missing))
    assert load_portfolio_config(force_reload=True) is None


def test_load_portfolio_config_weight_pct(tmp_path, monkeypatch):
    reset_portfolio_config_cache()
    path = tmp_path / "portfolio.json"
    path.write_text(
        json.dumps(
            {
                "name": "Test",
                "currency": "EUR",
                "valuation_mode": "weight_pct",
                "holdings": [
                    {"symbol": "VWCE.DE", "weight_pct": 60.0},
                    {"symbol": "AGGH.DE", "weight_pct": 40.0},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FUNTRADE_PORTFOLIO", str(path))
    cfg = load_portfolio_config(force_reload=True)
    assert cfg is not None
    assert cfg.name == "Test"
    assert cfg.valuation_mode == "weight_pct"
    assert cfg.total_weight_pct() == pytest.approx(100.0)
    assert cfg.symbols() == ("VWCE.DE", "AGGH.DE")


def test_load_portfolio_config_caches_on_second_call(tmp_path, monkeypatch):
    reset_portfolio_config_cache()
    path = tmp_path / "portfolio.json"
    path.write_text(
        json.dumps(
            {
                "valuation_mode": "weight_pct",
                "holdings": [{"symbol": "VWCE.DE", "weight_pct": 100.0}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FUNTRADE_PORTFOLIO", str(path))
    first = load_portfolio_config(force_reload=True)
    assert first is not None
    second = load_portfolio_config()
    assert second is first


def test_load_portfolio_config_requires_weight_when_mode_weight_pct(tmp_path, monkeypatch):
    reset_portfolio_config_cache()
    path = tmp_path / "portfolio.json"
    path.write_text(
        json.dumps(
            {
                "valuation_mode": "weight_pct",
                "holdings": [{"symbol": "VWCE.DE"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FUNTRADE_PORTFOLIO", str(path))
    with pytest.raises(ValueError, match="weight_pct"):
        load_portfolio_config(force_reload=True)
