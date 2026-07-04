import numpy as np
import pandas as pd

from funtrade.models.equilibrium import _fit_ou_parameters, _fit_seasonality
from funtrade.models.perturbation import signal_from_epsilon


def test_signal_from_epsilon_mean_reversion():
    assert signal_from_epsilon(2.5, 2.0, True) == 0  # long-only: no short
    assert signal_from_epsilon(-2.5, 2.0, True) == 1
    assert signal_from_epsilon(1.0, 2.0, True) == 0
    assert signal_from_epsilon(3.0, 2.0, False) == 0


def test_signal_from_epsilon_long_only_sell():
    assert signal_from_epsilon(2.5, 2.0, True, long_only=True, current_position=10) == -1
    assert signal_from_epsilon(2.5, 2.0, True, long_only=True, current_position=0) == 0


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


def test_fit_seasonality_daily_returns_coefficients():
    index = pd.date_range("2024-01-01", periods=60, freq="D", tz="UTC")
    log_prices = pd.Series(np.sin(np.arange(len(index)) / 7) + 3.0, index=index)
    coeffs = _fit_seasonality(log_prices)
    assert "intercept" in coeffs
    assert "dow_dummies" in coeffs
    assert "month_dummies" in coeffs
    assert "r_squared" in coeffs
