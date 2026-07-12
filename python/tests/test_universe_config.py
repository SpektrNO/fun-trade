"""Tests for config.json universe loading."""

from funtrade.config import Settings
from funtrade.universe_config import load_universe_config, reset_universe_config_cache


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


def test_config_aliases_override_builtin():
    from funtrade.data.symbols import resolve_fetch_ticker, symbol_aliases

    assert symbol_aliases()["MYFUND.XX"] == "0P00001234.IR"
    assert resolve_fetch_ticker("MYFUND.XX") == "0P00001234.IR"
