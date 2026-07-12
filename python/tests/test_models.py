import numpy as np
import pandas as pd
import pytest
from dataclasses import replace

from funtrade.config import Settings
from funtrade.models.equilibrium import (
    EquilibriumModel,
    _fit_ou_parameters,
    _fit_seasonality,
    _seasonal_values,
)
from funtrade.models.perturbation import (
    _Z_RETURN_CLIP,
    _z_return_from_fair_band,
    signal_from_epsilon,
    _compute_regime_validity,
    _compute_z_trend,
)


def test_equilibrium_residual_matches_log_price_over_fair(monkeypatch):
    index = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
    prices = pd.Series([100.0, 110.0, 90.0, 105.0, 95.0], index=index)
    model = EquilibriumModel(
        symbol="TEST",
        kappa=0.1,
        mu=0.0,
        sigma=0.05,
        half_life_days=10.0,
        seasonal_coeffs={
            "intercept": 0.0,
            "dow_dummies": {},
            "fourier_annual": {"K": 1, "cos": [0.0], "sin": [0.0]},
        },
    )
    h0_adj = pd.Series([0.0, 0.02, -0.01, 0.0, 0.01], index=index)
    z_trend = pd.Series([0.0, 0.05, 0.10, -0.02, 0.0], index=index)

    monkeypatch.setattr(
        "funtrade.models.equilibrium.compute_h0_fundamental_adjustment",
        lambda *args, **kwargs: h0_adj,
    )

    settings = replace(
        Settings.from_env(),
        trend_enable=True,
        trend_fair_value_weight=1.0,
    )
    band = model.equilibrium_band(prices, settings=settings, z_trend=z_trend)
    log_ratio = np.log(prices.clip(lower=1e-6) / band["equilibrium"].clip(lower=1e-6))
    np.testing.assert_allclose(band["residual"], log_ratio.values, rtol=1e-9)
    np.testing.assert_allclose(band["residual"] / model.sigma, log_ratio.values / model.sigma)


def test_z_return_from_fair_band_uses_two_sigma_and_clip():
    residual = pd.Series([0.0, 0.04, -0.20, 0.30])
    sigma = 0.01
    z = _z_return_from_fair_band(residual, sigma)
    # 0.04 / (2*0.01) = 2.0 at upper band edge
    assert z.iloc[1] == pytest.approx(2.0)
    assert z.iloc[0] == pytest.approx(0.0)
    # -0.20 / 0.02 = -10 → clipped to -8
    assert z.iloc[2] == -_Z_RETURN_CLIP
    assert z.iloc[3] == _Z_RETURN_CLIP


def test_signal_from_epsilon_mean_reversion():
    assert signal_from_epsilon(2.5, 2.0, True) == 0  # long-only: no short
    assert signal_from_epsilon(-2.5, 2.0, True) == 1
    assert signal_from_epsilon(1.0, 2.0, True) == 0
    assert signal_from_epsilon(3.0, 2.0, False) == 0


def test_signal_from_epsilon_long_only_sell():
    assert signal_from_epsilon(2.5, 2.0, True, long_only=True, current_position=10) == -1
    assert signal_from_epsilon(2.5, 2.0, True, long_only=True, current_position=0) == 0


def test_signal_from_epsilon_exit_when_regime_invalid_if_holding():
    assert signal_from_epsilon(2.5, 2.0, False, long_only=True, current_position=10) == -1
    assert signal_from_epsilon(-2.5, 2.0, False, long_only=True, current_position=0) == 0


def test_signal_from_epsilon_trend_gate_blocks_sell_in_uptrend():
    assert signal_from_epsilon(
        2.5, 2.0, True, long_only=True, current_position=10,
        z_trend=1.0, trend_gate_sells=True, trend_gate_z=0.5,
    ) == 0
    assert signal_from_epsilon(
        2.5, 2.0, True, long_only=True, current_position=10,
        z_trend=1.0, trend_gate_sells=False, trend_gate_z=0.5,
    ) == -1


def test_compute_z_trend_positive_when_above_sma():
    index = pd.date_range("2024-01-01", periods=300, freq="D", tz="UTC")
    price = pd.Series(100.0, index=index)
    price.iloc[-40:] = 130.0
    z = _compute_z_trend(price, None, lookback=50, use_benchmark=False)
    assert float(z.iloc[-1]) > 0


def test_fit_ou_parameters_synthetic():
    rng = np.random.default_rng(42)
    kappa_true = 0.1
    mu_true = 0.0
    sigma_true = 0.05
    dt = 1.0
    n = 500
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = x[i - 1] + kappa_true * (mu_true - x[i - 1]) * dt + sigma_true * np.sqrt(dt) * rng.normal()

    kappa, mu, sigma = _fit_ou_parameters(pd.Series(x), dt_days=dt)
    assert 0.05 < kappa < 0.2
    assert abs(mu) < 0.1
    assert 0.01 < sigma < 0.15


def test_fit_ou_parameters_anchors_mu_when_near_unit_root():
    # Strong trend: φ→1; μ from intercept/(1-φ) would explode without anchoring.
    x = np.linspace(0.0, 2.0, 400)
    kappa, mu, sigma = _fit_ou_parameters(pd.Series(x), dt_days=1.0)
    assert abs(mu) < 2.5
    assert sigma > 0
    assert kappa > 0


def test_regime_validity_skips_liquidity_when_volume_missing():
    index = pd.date_range("2024-01-01", periods=30, freq="D", tz="UTC")
    magnitude = pd.Series(1.0, index=index)
    price = pd.Series(1000.0, index=index)
    volume = pd.Series(0.0, index=index)
    valid = _compute_regime_validity(magnitude, price, volume, spike_sigma=3.0, consecutive_bars=3, min_daily_volume_eur=100_000)
    assert valid.all()


def test_fit_seasonality_daily_returns_coefficients():
    index = pd.date_range("2024-01-01", periods=60, freq="D", tz="UTC")
    log_prices = pd.Series(np.sin(np.arange(len(index)) / 7) + 3.0, index=index)
    coeffs = _fit_seasonality(log_prices)
    assert "intercept" in coeffs
    assert "dow_dummies" in coeffs
    assert "fourier_annual" in coeffs
    assert coeffs["fourier_annual"]["K"] >= 1
    assert "r_squared" in coeffs


def test_fourier_seasonality_is_continuous_across_month_boundary():
    index = pd.date_range("2026-06-28", "2026-07-03", freq="D", tz="UTC")
    log_prices = pd.Series(np.linspace(5.0, 5.05, len(index)), index=index)
    coeffs = _fit_seasonality(log_prices)
    season = _seasonal_values(index, coeffs)
    jun30 = season[index.get_loc("2026-06-30")]
    jul1 = season[index.get_loc("2026-07-01")]
    assert abs(jul1 - jun30) < 0.02
