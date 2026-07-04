"""H0 equilibrium model: seasonal Ornstein-Uhlenbeck calibration (daily bars)."""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm

from funtrade.data.factors import compute_h0_fundamental_adjustment
from funtrade.data.loader import MARKET_ADJ_CLOSE, load_price_bars, save_equilibrium_params
from funtrade.config import Settings

# When OU φ→1, intercept/(1-φ) blows up on trending ETFs; anchor μ to recent levels instead.
_OU_PHI_MU_ANCHOR = 0.995
_MU_ANCHOR_DAYS = 252
_MAX_HALF_LIFE_DAYS = 500.0


def _calibration_lookback_days() -> int:
    return int(os.getenv("H0_CALIBRATION_DAYS", "504"))


@dataclass
class EquilibriumModel:
    symbol: str
    kappa: float
    mu: float
    sigma: float
    half_life_days: float
    seasonal_coeffs: dict

    def seasonal_component(self, index: pd.DatetimeIndex) -> np.ndarray:
        dow = index.dayofweek
        month = index.month
        coeffs = self.seasonal_coeffs
        intercept = coeffs.get("intercept", 0.0)
        dow_coefs = coeffs.get("dow_dummies", {})
        month_coefs = coeffs.get("month_dummies", {})

        values = np.full(len(index), intercept, dtype=float)
        for i, _ts in enumerate(index):
            d_key = str(int(dow[i]))
            m_key = str(int(month[i]))
            values[i] += dow_coefs.get(d_key, 0.0)
            values[i] += month_coefs.get(m_key, 0.0)
        return values

    def deseasonalize(self, prices: pd.Series) -> pd.Series:
        log_prices = np.log(prices.clip(lower=0.01))
        season = self.seasonal_component(prices.index)
        return pd.Series(log_prices.values - season, index=prices.index, name="x")

    def equilibrium_band(
        self,
        prices: pd.Series,
        *,
        symbol: str | None = None,
        settings: Settings | None = None,
        z_trend: pd.Series | None = None,
    ) -> pd.DataFrame:
        settings = settings or Settings.from_env()
        season = self.seasonal_component(prices.index)
        x = self.deseasonalize(prices)
        h0_adj = compute_h0_fundamental_adjustment(symbol or self.symbol, prices.index, settings=settings)
        trend_adj = pd.Series(0.0, index=prices.index)
        if settings.trend_enable and settings.trend_fair_value_weight != 0.0 and z_trend is not None:
            trend_adj = settings.trend_fair_value_weight * z_trend.reindex(prices.index).fillna(0.0)
        log_mean = season + self.mu + h0_adj + trend_adj
        upper = np.exp(log_mean + 2 * self.sigma)
        lower = np.exp(log_mean - 2 * self.sigma)
        return pd.DataFrame(
            {
                "price": prices,
                "equilibrium": np.exp(log_mean),
                "upper": upper,
                "lower": lower,
                "residual": x - self.mu,
            },
            index=prices.index,
        )


def _fit_seasonality(log_prices: pd.Series) -> dict:
    index = log_prices.index
    dow = index.dayofweek
    month = index.month

    dow_dummies = pd.get_dummies(dow, prefix="d", drop_first=True)
    month_dummies = pd.get_dummies(month, prefix="m", drop_first=True)
    X = sm.add_constant(pd.concat([dow_dummies, month_dummies], axis=1).astype(float))
    model = sm.OLS(log_prices.values.astype(float), X.values.astype(float)).fit()
    params = model.params

    intercept = float(params[0])
    dow_coefs = {}
    month_coefs = {}

    for col, coef in zip(X.columns[1:], params[1:], strict=False):
        if col.startswith("d_"):
            dow_coefs[col[2:]] = float(coef)
        elif col.startswith("m_"):
            month_coefs[col[2:]] = float(coef)

    return {
        "intercept": intercept,
        "dow_dummies": dow_coefs,
        "month_dummies": month_coefs,
        "r_squared": float(model.rsquared),
    }


def _fit_ou_parameters(
    residuals: pd.Series,
    dt_days: float = 1.0,
    *,
    mu_anchor_days: int = _MU_ANCHOR_DAYS,
) -> tuple[float, float, float]:
    x = residuals.dropna().values
    if len(x) < 10:
        raise ValueError("Insufficient data for OU calibration")

    x_lag = x[:-1]
    x_cur = x[1:]
    X = sm.add_constant(x_lag)
    result = sm.OLS(x_cur, X).fit()
    phi = float(result.params[1])
    intercept = float(result.params[0])

    phi_raw = phi
    phi = np.clip(phi, 1e-6, 0.9999)
    kappa = -np.log(phi) / dt_days
    if phi_raw >= _OU_PHI_MU_ANCHOR:
        # Trending / near-unit-root: long-run OU mean is unstable; use recent deseasonalized level.
        tail = residuals.dropna().tail(mu_anchor_days)
        mu = float(tail.median()) if len(tail) else float(np.median(x))
    else:
        mu = intercept / (1 - phi)
    residuals_ou = x_cur - (intercept + phi * x_lag)
    sigma = float(np.std(residuals_ou, ddof=1) / np.sqrt(dt_days))

    return kappa, mu, max(sigma, 1e-6)


def calibrate_equilibrium(
    symbol: str,
    *,
    market: str = MARKET_ADJ_CLOSE,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    persist: bool = True,
    settings: Settings | None = None,
) -> EquilibriumModel:
    settings = settings or Settings.from_env()
    df = load_price_bars(symbol, market, start=start, end=end, settings=settings)
    if df.empty or len(df) < 60:
        raise ValueError(f"Insufficient price data for symbol {symbol}")

    lookback = _calibration_lookback_days()
    if start is None and len(df) > lookback:
        df = df.tail(lookback)

    prices = df["price"].astype(float)
    log_prices = np.log(prices.clip(lower=0.01))
    log_prices = pd.Series(log_prices.values, index=prices.index, name="log_price")

    seasonal_coeffs = _fit_seasonality(log_prices)
    season = np.full(len(log_prices), seasonal_coeffs["intercept"])
    dow = log_prices.index.dayofweek
    month = log_prices.index.month
    for i, _ts in enumerate(log_prices.index):
        season[i] += seasonal_coeffs["dow_dummies"].get(str(int(dow[i])), 0.0)
        season[i] += seasonal_coeffs["month_dummies"].get(str(int(month[i])), 0.0)

    residuals = pd.Series(log_prices.values - season, index=log_prices.index, name="residual")
    kappa, mu, sigma = _fit_ou_parameters(residuals, dt_days=1.0)
    half_life = np.log(2) / kappa
    if half_life > _MAX_HALF_LIFE_DAYS:
        mu = float(residuals.tail(_MU_ANCHOR_DAYS).median())
        half_life = float(_MAX_HALF_LIFE_DAYS)
        kappa = np.log(2) / half_life

    model = EquilibriumModel(
        symbol=symbol,
        kappa=kappa,
        mu=mu,
        sigma=sigma,
        half_life_days=float(half_life),
        seasonal_coeffs=seasonal_coeffs,
    )

    if persist:
        save_equilibrium_params(
            symbol, kappa, mu, sigma, float(half_life), seasonal_coeffs, settings=settings
        )

    return model


def load_or_calibrate(symbol: str, *, settings: Settings | None = None, **kwargs) -> EquilibriumModel:
    from funtrade.data.loader import load_latest_equilibrium_params

    settings = settings or Settings.from_env()
    params = load_latest_equilibrium_params(symbol, settings=settings)
    if params is None:
        return calibrate_equilibrium(symbol, settings=settings, **kwargs)

    return EquilibriumModel(
        symbol=symbol,
        kappa=params["kappa"],
        mu=params["mu"],
        sigma=params["sigma"],
        half_life_days=params["half_life_days"],
        seasonal_coeffs=params["seasonal_coeffs"],
    )
