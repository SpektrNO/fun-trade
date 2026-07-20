import json

import pytest

from funtrade.portfolio_config import (
    discover_portfolio_files,
    load_portfolio_config,
    reset_portfolio_config_cache,
)


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


def test_load_portfolio_config_parses_value_nok_and_value_us(tmp_path, monkeypatch):
    reset_portfolio_config_cache()
    path = tmp_path / "portfolio.json"
    path.write_text(
        json.dumps(
            {
                "name": "Test",
                "currency": "NOK",
                "valuation_mode": "weight_pct",
                "holdings": [
                    {
                        "symbol": "VWCE.DE",
                        "weight_pct": 60.0,
                        "value_nok": 100000,
                        "value_us": 12000,
                        "note": "mixed currency values",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FUNTRADE_PORTFOLIO", str(path))
    cfg = load_portfolio_config(force_reload=True)
    assert cfg is not None
    (h,) = cfg.holdings
    assert h.value_nok == pytest.approx(100000.0)
    assert h.value_usd == pytest.approx(12000.0)


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


def test_discover_portfolio_files(tmp_path, monkeypatch):
    reset_portfolio_config_cache()
    monkeypatch.setattr("funtrade.portfolio_config.repo_root", lambda: tmp_path)
    (tmp_path / "portfolio_private.json").write_text("{}", encoding="utf-8")
    (tmp_path / "portfolio_spektr.json").write_text("{}", encoding="utf-8")
    (tmp_path / "portfolio.json").write_text("{}", encoding="utf-8")
    names = [p.name for p in discover_portfolio_files()]
    assert names == ["portfolio.json", "portfolio_private.json", "portfolio_spektr.json"]


def test_load_portfolio_config_from_filename_only(tmp_path, monkeypatch):
    reset_portfolio_config_cache()
    monkeypatch.setattr("funtrade.portfolio_config.repo_root", lambda: tmp_path)
    path = tmp_path / "portfolio_spektr.json"
    path.write_text(
        json.dumps(
            {
                "name": "Spektr",
                "valuation_mode": "weight_pct",
                "holdings": [{"symbol": "VWCE.DE", "weight_pct": 100.0}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "python").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path / "python")
    cfg = load_portfolio_config("portfolio_spektr.json")
    assert cfg is not None
    assert cfg.name == "Spektr"


def test_load_portfolio_config_from_explicit_path(tmp_path, monkeypatch):
    reset_portfolio_config_cache()
    path = tmp_path / "portfolio_private.json"
    path.write_text(
        json.dumps(
            {
                "name": "Private",
                "valuation_mode": "weight_pct",
                "holdings": [{"symbol": "VWCE.DE", "weight_pct": 100.0}],
            }
        ),
        encoding="utf-8",
    )
    cfg = load_portfolio_config(path)
    assert cfg is not None
    assert cfg.name == "Private"
    assert cfg.source_path == path.resolve()


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
