"""Asset-class-specific H₀ calibration parameters."""

import numpy as np
import pandas as pd
import pytest
from dataclasses import replace

from funtrade.config import Settings
from funtrade.models.equilibrium import (
    EquilibriumModel,
    _effective_sigma,
    _fit_seasonality,
)
from funtrade.models.perturbation import _z_return_from_fair_band
from funtrade.universe_config import load_universe_config, reset_universe_config_cache


def test_universe_config_h0_defaults_by_asset_class():
    reset_universe_config_cache()
    cfg = load_universe_config(force_reload=True)
    etf = cfg.etf
    fund = cfg.mutual_fund
    share = cfg.share
    assert etf.h0_sigma_floor == 0.0
    assert etf.h0_band_sigma_mult == 2.0
    assert etf.h0_seasonal_dow is True
    assert fund.h0_sigma_floor == 0.015
    assert fund.h0_band_sigma_mult == 2.5
    assert fund.h0_seasonal_dow is False
    assert fund.h0_macro_fair_scale == 0.5
    assert share.h0_mu_anchor_days == 189


def test_settings_for_symbol_applies_h0_calibration_params():
    settings = Settings.from_env()
    etf = settings.for_symbol("VWCE.DE")
    fund = settings.for_symbol("NO0010336977")
    assert etf.h0_sigma_floor == 0.0
    assert fund.h0_sigma_floor == 0.015
    assert fund.h0_band_sigma_mult == 2.5
    assert fund.h0_seasonal_dow is False
    assert fund.h0_macro_fair_scale == 0.5


def test_effective_sigma_applies_floor_and_realized_blend():
    index = pd.date_range("2024-01-01", periods=120, freq="D", tz="UTC")
    rng = np.random.default_rng(0)
    residuals = pd.Series(np.cumsum(rng.normal(0, 0.002, len(index))), index=index)
    out = _effective_sigma(0.005, residuals, sigma_floor=0.015, realized_vol_sigma_frac=0.75)
    assert out >= 0.015


def test_fit_seasonality_can_omit_dow():
    index = pd.date_range("2024-01-01", periods=400, freq="D", tz="UTC")
    log_prices = pd.Series(np.log(100 + np.arange(len(index)) * 0.01), index=index)
    coeffs = _fit_seasonality(log_prices, include_dow=False)
    assert coeffs["seasonal_dow"] is False
    assert coeffs["dow_dummies"] == {}


def test_equilibrium_band_macro_fair_scale(monkeypatch):
    index = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
    prices = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], index=index)
    model = EquilibriumModel(
        symbol="TEST",
        kappa=0.1,
        mu=0.0,
        sigma=0.05,
        half_life_days=10.0,
        seasonal_coeffs={
            "intercept": np.log(100.0),
            "dow_dummies": {},
            "fourier_annual": {"K": 1, "cos": [0.0], "sin": [0.0]},
        },
    )
    h0_adj = pd.Series([0.10] * len(index), index=index)
    monkeypatch.setattr(
        "funtrade.models.equilibrium.compute_h0_fundamental_adjustment",
        lambda *args, **kwargs: h0_adj,
    )
    settings = replace(Settings.from_env(), h0_macro_fair_scale=0.5, h0_band_sigma_mult=2.0)
    band = model.equilibrium_band(prices, settings=settings)
    full = model.equilibrium_band(prices, settings=replace(settings, h0_macro_fair_scale=1.0))
    assert band["equilibrium"].iloc[-1] < full["equilibrium"].iloc[-1]


def test_equilibrium_band_uses_configurable_sigma_mult(monkeypatch):
    index = pd.date_range("2024-01-01", periods=3, freq="D", tz="UTC")
    prices = pd.Series([100.0, 100.0, 100.0], index=index)
    model = EquilibriumModel(
        symbol="TEST",
        kappa=0.1,
        mu=0.0,
        sigma=0.05,
        half_life_days=10.0,
        seasonal_coeffs={
            "intercept": np.log(100.0),
            "dow_dummies": {},
            "fourier_annual": {"K": 1, "cos": [0.0], "sin": [0.0]},
        },
    )
    monkeypatch.setattr(
        "funtrade.models.equilibrium.compute_h0_fundamental_adjustment",
        lambda *args, **kwargs: pd.Series(0.0, index=index),
    )
    narrow = model.equilibrium_band(
        prices, settings=replace(Settings.from_env(), h0_band_sigma_mult=2.0)
    )
    wide = model.equilibrium_band(
        prices, settings=replace(Settings.from_env(), h0_band_sigma_mult=3.0)
    )
    assert wide["upper"].iloc[0] > narrow["upper"].iloc[0]
    assert wide["lower"].iloc[0] < narrow["lower"].iloc[0]


def test_z_return_respects_band_sigma_mult():
    residual = pd.Series([-0.04])
    sigma = 0.01
    z_narrow = _z_return_from_fair_band(residual, sigma, band_sigma_mult=2.0)
    z_wide = _z_return_from_fair_band(residual, sigma, band_sigma_mult=2.5)
    assert z_narrow.iloc[0] == pytest.approx(-2.0)
    assert z_wide.iloc[0] == pytest.approx(-1.6)
    assert abs(z_wide.iloc[0]) < abs(z_narrow.iloc[0])
