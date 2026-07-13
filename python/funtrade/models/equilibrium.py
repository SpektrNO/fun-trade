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
_DAYS_PER_YEAR = 365.25


def _annual_fourier_harmonics() -> int:
    return max(1, int(os.getenv("H0_FOURIER_HARMONICS", "2")))


def _annual_phase(index: pd.DatetimeIndex) -> np.ndarray:
    """Day-of-year phase in [0, 2π) for smooth annual seasonality."""
    day = index.dayofyear.to_numpy(dtype=float) - 1.0
    return 2.0 * np.pi * day / _DAYS_PER_YEAR


def _annual_fourier_design(index: pd.DatetimeIndex, k: int) -> np.ndarray:
    phase = _annual_phase(index)
    cols: list[np.ndarray] = []
    for harmonic in range(1, k + 1):
        cols.append(np.cos(harmonic * phase))
        cols.append(np.sin(harmonic * phase))
    return np.column_stack(cols)


def _seasonal_values(index: pd.DatetimeIndex, coeffs: dict) -> np.ndarray:
    """Log-price seasonal component (DOW dummies + annual Fourier or legacy month dummies)."""
    intercept = coeffs.get("intercept", 0.0)
    dow_coefs = coeffs.get("dow_dummies", {})
    values = np.full(len(index), intercept, dtype=float)

    dow = index.dayofweek
    for i, _ts in enumerate(index):
        values[i] += dow_coefs.get(str(int(dow[i])), 0.0)

    fourier = coeffs.get("fourier_annual")
    if fourier:
        k = int(fourier.get("K", 0))
        cos_coefs = fourier.get("cos", [])
        sin_coefs = fourier.get("sin", [])
        phase = _annual_phase(index)
        for harmonic in range(1, k + 1):
            values += cos_coefs[harmonic - 1] * np.cos(harmonic * phase)
            values += sin_coefs[harmonic - 1] * np.sin(harmonic * phase)
    else:
        month_coefs = coeffs.get("month_dummies", {})
        month = index.month
        for i, _ts in enumerate(index):
            values[i] += month_coefs.get(str(int(month[i])), 0.0)

    return values


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
        return _seasonal_values(index, self.seasonal_coeffs)

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
        macro_scale = float(getattr(settings, "h0_macro_fair_scale", 1.0))
        if macro_scale != 1.0:
            h0_adj = h0_adj * macro_scale
        trend_adj = pd.Series(0.0, index=prices.index)
        if settings.trend_enable and settings.trend_fair_value_weight != 0.0 and z_trend is not None:
            trend_adj = settings.trend_fair_value_weight * z_trend.reindex(prices.index).fillna(0.0)
        log_mean = season + self.mu + h0_adj + trend_adj
        band_mult = float(getattr(settings, "h0_band_sigma_mult", 2.0))
        upper = np.exp(log_mean + band_mult * self.sigma)
        lower = np.exp(log_mean - band_mult * self.sigma)
        # Log-distance from fair (H₀) — used as z_return input so ε matches the chart band.
        residual = x - self.mu - h0_adj - trend_adj
        return pd.DataFrame(
            {
                "price": prices,
                "equilibrium": np.exp(log_mean),
                "upper": upper,
                "lower": lower,
                "residual": residual,
            },
            index=prices.index,
        )


def _fit_seasonality(log_prices: pd.Series, *, include_dow: bool = True) -> dict:
    index = log_prices.index
    k = _annual_fourier_harmonics()
    fourier = _annual_fourier_design(index, k)

    if include_dow:
        dow = index.dayofweek
        dow_dummies = pd.get_dummies(dow, prefix="d", drop_first=True)
        X = sm.add_constant(
            np.column_stack([dow_dummies.to_numpy(dtype=float), fourier]),
            has_constant="add",
        )
        model = sm.OLS(log_prices.values.astype(float), X).fit()
        params = model.params

        intercept = float(params[0])
        dow_coefs: dict[str, float] = {}
        n_dow = dow_dummies.shape[1]
        for col, coef in zip(dow_dummies.columns, params[1 : 1 + n_dow], strict=False):
            dow_coefs[col[2:]] = float(coef)
        fourier_params = params[1 + n_dow :]
    else:
        X = sm.add_constant(fourier, has_constant="add")
        model = sm.OLS(log_prices.values.astype(float), X).fit()
        params = model.params
        intercept = float(params[0])
        dow_coefs = {}
        fourier_params = params[1:]

    cos_coefs: list[float] = []
    sin_coefs: list[float] = []
    for harmonic in range(k):
        cos_coefs.append(float(fourier_params[harmonic * 2]))
        sin_coefs.append(float(fourier_params[harmonic * 2 + 1]))

    return {
        "intercept": intercept,
        "dow_dummies": dow_coefs,
        "fourier_annual": {"K": k, "cos": cos_coefs, "sin": sin_coefs},
        "seasonal_model": "fourier",
        "seasonal_dow": include_dow,
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


def _effective_sigma(
    sigma: float,
    residuals: pd.Series,
    *,
    sigma_floor: float,
    realized_vol_sigma_frac: float,
) -> float:
    """Raise σ for smooth NAV / low-vol series so z_return does not clip constantly."""
    out = max(float(sigma), 1e-6)
    if sigma_floor > 0:
        out = max(out, sigma_floor)
    if realized_vol_sigma_frac > 0:
        diffs = residuals.diff().dropna()
        if len(diffs) >= 20:
            realized = float(diffs.std(ddof=1))
            out = max(out, realized_vol_sigma_frac * realized)
    return out


def calibrate_equilibrium(
    symbol: str,
    *,
    market: str = MARKET_ADJ_CLOSE,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    persist: bool = True,
    settings: Settings | None = None,
) -> EquilibriumModel:
    settings = (settings or Settings.from_env()).for_symbol(symbol)
    df = load_price_bars(symbol, market, start=start, end=end, settings=settings)
    if df.empty or len(df) < 60:
        raise ValueError(f"Insufficient price data for symbol {symbol}")

    lookback = settings.h0_calibration_days
    if start is None and len(df) > lookback:
        df = df.tail(lookback)

    prices = df["price"].astype(float)
    log_prices = np.log(prices.clip(lower=0.01))
    log_prices = pd.Series(log_prices.values, index=prices.index, name="log_price")

    seasonal_coeffs = _fit_seasonality(log_prices, include_dow=settings.h0_seasonal_dow)
    season = _seasonal_values(log_prices.index, seasonal_coeffs)

    residuals = pd.Series(log_prices.values - season, index=log_prices.index, name="residual")
    kappa, mu, sigma = _fit_ou_parameters(
        residuals,
        dt_days=1.0,
        mu_anchor_days=settings.h0_mu_anchor_days,
    )
    half_life = np.log(2) / kappa
    if half_life > _MAX_HALF_LIFE_DAYS:
        mu = float(residuals.tail(settings.h0_mu_anchor_days).median())
        half_life = float(_MAX_HALF_LIFE_DAYS)
        kappa = np.log(2) / half_life

    sigma = _effective_sigma(
        sigma,
        residuals,
        sigma_floor=settings.h0_sigma_floor,
        realized_vol_sigma_frac=settings.h0_realized_vol_sigma_frac,
    )

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
    settings = settings.for_symbol(symbol)
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
