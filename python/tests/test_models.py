import numpy as np
import pandas as pd

from funtrade.models.equilibrium import _fit_ou_parameters, _fit_seasonality
from funtrade.models.perturbation import signal_from_epsilon, _compute_regime_validity


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
    assert "month_dummies" in coeffs
    assert "r_squared" in coeffs
