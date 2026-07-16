"""Tests for config.json universe loading."""

from funtrade.cli import _resolve_watchlist_symbols
from funtrade.config import Settings
from funtrade.universe_config import load_universe_config, parse_asset_classes, reset_universe_config_cache


def test_load_universe_config_strategy_router():
    reset_universe_config_cache()
    cfg = load_universe_config(force_reload=True)
    assert cfg.strategy_router.trend_z_min == 0.5
    assert cfg.strategy_router.default_model == "perturbation"


def test_load_universe_config_watchlist():
    reset_universe_config_cache()
    cfg = load_universe_config(force_reload=True)
    assert "VWCE.DE" in cfg.etf.symbols
    assert "NO0010336977" in cfg.mutual_fund.symbols
    assert cfg.class_of("NO0010336977") == "mutual_fund"
    assert cfg.class_of("VWCE.DE") == "etf"
    assert cfg.class_of("EQNR.OL") == "share"
    assert len(cfg.watchlist()) == 4


def test_settings_for_symbol_applies_asset_class():
    settings = Settings.from_env()
    etf = settings.for_symbol("VWCE.DE")
    fund = settings.for_symbol("NO0010336977")
    share = settings.for_symbol("EQNR.OL")
    assert etf.epsilon_threshold == 0.5
    assert fund.min_daily_volume_eur == 0.0
    assert fund.w_volume == 0.0
    assert share.regime_spike_sigma == 3.5
    assert share.trend_gate_sells is False
    assert etf.h0_calibration_days == 400
    assert fund.h0_calibration_days == 600
    assert share.h0_calibration_days == 365
    assert fund.h0_sigma_floor == 0.015
    assert fund.h0_seasonal_dow is False


def test_parse_asset_classes_accepts_aliases():
    assert parse_asset_classes("ETF SHARE") == ("etf", "share")
    assert parse_asset_classes(["mutual_fund", "FUNDS"]) == ("mutual_fund",)


def test_symbols_for_classes_filters_watchlist():
    reset_universe_config_cache()
    cfg = load_universe_config(force_reload=True)
    assert cfg.symbols_for_classes(("etf", "share")) == list(cfg.etf.symbols) + list(cfg.share.symbols)
    assert cfg.symbols_for_classes(("mutual_fund",)) == list(cfg.mutual_fund.symbols)


def test_resolve_watchlist_symbols_by_class():
    settings = Settings.from_env()
    symbols = _resolve_watchlist_symbols(settings, symbol=None, symbols=None, classes=["ETF"])
    assert symbols is not None
    assert "VWCE.DE" in symbols
    assert "NO0010336977" not in symbols


def test_config_aliases_override_builtin():
    from funtrade.data.symbols import resolve_fetch_ticker, symbol_aliases

    assert symbol_aliases()["MYFUND.XX"] == "0P00001234.IR"
    assert resolve_fetch_ticker("MYFUND.XX") == "0P00001234.IR"


def test_load_universe_from_external_file():
    reset_universe_config_cache()
    cfg = load_universe_config(force_reload=True)
    assert cfg.universe_path is not None
    assert cfg.universe_path.name == "universe.json"
    assert "VWCE.DE" in cfg.etf.symbols
    assert cfg.aliases["MYFUND.XX"] == "0P00001234.IR"
