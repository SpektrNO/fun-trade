"""H1 perturbation detection and regime invalidation (daily UCITS ETFs)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from funtrade.config import Settings
from funtrade.data.factors import blend_epsilon, compute_h1_component_scores
from funtrade.data.loader import MARKET_ADJ_CLOSE, load_price_bars, save_perturbation_event, upsert_perturbation_daily
from funtrade.models.components import DEFAULT_H1_WEIGHTS, sector_etf_for
from funtrade.models.equilibrium import EquilibriumModel, load_or_calibrate

# H₀ equilibrium band is ±Nσ in log-price space (N = h0_band_sigma_mult per asset class).
# Express fair-distance in those units (±1 = at the band edge) before blending ε.
_BAND_SIGMA_MULT = 2.0  # default when settings unavailable
_Z_RETURN_CLIP = 8.0


def _z_return_from_fair_band(
    residual: pd.Series,
    sigma: float,
    *,
    band_sigma_mult: float = _BAND_SIGMA_MULT,
) -> pd.Series:
    """log(price/fair) normalized to band σ, clipped for stable ε magnitude."""
    denom = max(float(band_sigma_mult) * float(sigma), 1e-8)
    band_z = residual / denom
    return band_z.clip(-_Z_RETURN_CLIP, _Z_RETURN_CLIP)


@dataclass
class PerturbationResult:
    time: pd.Timestamp
    symbol: str
    asset_class: str
    epsilon: float
    magnitude: float
    regime_valid: bool
    z_return: float
    z_volume: float
    z_rel_strength: float
    h1_components: dict
    inputs: dict


def _zscore(series: pd.Series, window: int = 20) -> pd.Series:
    rolling_mean = series.rolling(window, min_periods=5).mean()
    rolling_std = series.rolling(window, min_periods=5).std().clip(lower=1e-6)
    return (series - rolling_mean) / rolling_std


def _compute_z_trend(
    price: pd.Series,
    bench_price: pd.Series | None,
    *,
    lookback: int,
    use_benchmark: bool,
) -> pd.Series:
    """Z-scored distance from SMA — positive = above medium-term trend (uptrend expectation)."""
    series = bench_price if use_benchmark and bench_price is not None and not bench_price.empty else price
    min_periods = max(20, lookback // 4)
    sma = series.rolling(lookback, min_periods=min_periods).mean()
    deviation = (series / sma.clip(lower=1e-6) - 1.0).fillna(0.0)
    if use_benchmark and bench_price is not None:
        deviation = deviation.reindex(price.index, method="ffill").fillna(0.0)
    return _zscore(deviation, window=252)


def compute_perturbation_series(
    symbol: str,
    *,
    benchmark_symbol: str | None = None,
    weights: tuple[float, float, float] | None = None,
    settings: Settings | None = None,
    equilibrium: EquilibriumModel | None = None,
) -> pd.DataFrame:
    if settings is None:
        settings = Settings.from_env().for_symbol(symbol)
    if weights is None:
        weights = settings.perturbation_weights()
    equilibrium = equilibrium or load_or_calibrate(symbol, settings=settings)

    df = load_price_bars(symbol, MARKET_ADJ_CLOSE, settings=settings)
    if df.empty:
        return pd.DataFrame()

    price = df["price"].astype(float)

    bench_sym = benchmark_symbol or sector_etf_for(symbol, settings.benchmark)
    bench_df = load_price_bars(bench_sym, MARKET_ADJ_CLOSE, settings=settings)
    bench_price = None
    if not bench_df.empty:
        bench_price = bench_df["price"].astype(float).reindex(price.index, method="ffill")

    z_trend = pd.Series(0.0, index=df.index)
    if settings.trend_enable:
        z_trend = _compute_z_trend(
            price,
            bench_price,
            lookback=settings.trend_lookback_days,
            use_benchmark=settings.trend_use_benchmark,
        )

    band = equilibrium.equilibrium_band(price, symbol=symbol, settings=settings, z_trend=z_trend)
    z_return = _z_return_from_fair_band(
        band["residual"],
        equilibrium.sigma,
        band_sigma_mult=settings.h0_band_sigma_mult,
    )

    volume = df["volume"].astype(float) if "volume" in df.columns else pd.Series(0.0, index=df.index)
    if volume.notna().sum() > 5:
        z_volume = _zscore(volume.fillna(volume.median()))
    else:
        z_volume = pd.Series(0.0, index=df.index)

    if bench_price is not None:
        rel_ret = price.pct_change().fillna(0.0) - bench_price.pct_change().fillna(0.0)
        z_rel_strength = _zscore(rel_ret.fillna(0.0))
    else:
        z_rel_strength = pd.Series(0.0, index=df.index)

    # Realized vol spike
    ret = price.pct_change().fillna(0.0)
    vol_20 = ret.rolling(20, min_periods=5).std()
    vol_252 = ret.rolling(252, min_periods=20).std().clip(lower=1e-6)
    z_vol = ((vol_20 / vol_252) - 1.0).fillna(0.0)

    h1_scores = compute_h1_component_scores(symbol, df.index, settings=settings)
    h1_scores["z_vol"] = z_vol

    blend_weights = dict(DEFAULT_H1_WEIGHTS)
    if weights != (0.35, 0.10, 0.25):
        blend_weights["z_return"] = weights[0]
        blend_weights["z_volume"] = weights[1]
        blend_weights["z_rel_strength"] = weights[2]

    epsilon = blend_epsilon(z_return, z_volume, z_rel_strength, h1_scores, weights=blend_weights)
    if settings.trend_enable and settings.trend_epsilon_weight != 0.0:
        # Uptrend (z_trend > 0) lowers ε → less eager to sell on mean-reversion alone.
        epsilon = epsilon - settings.trend_epsilon_weight * z_trend
    magnitude = epsilon.abs()

    regime_valid = _compute_regime_validity(
        magnitude,
        price,
        volume,
        settings.regime_spike_sigma,
        settings.regime_consecutive_bars,
        settings.min_daily_volume_eur,
    )

    out = {
        "epsilon": epsilon,
        "magnitude": magnitude,
        "z_return": z_return,
        "z_volume": z_volume,
        "z_rel_strength": z_rel_strength,
        "z_vol": z_vol,
        "z_trend": z_trend,
        "regime_valid": regime_valid,
        "price": price,
    }
    for col in h1_scores.columns:
        out[f"h1_{col}"] = h1_scores[col]
    return pd.DataFrame(out, index=df.index)


def compute_latest_z_trend(
    symbol: str,
    *,
    settings: Settings | None = None,
    benchmark_symbol: str | None = None,
) -> float:
    """Latest z_trend only — tail-sliced price load for recommendations (not full ε recompute)."""
    if settings is None:
        settings = Settings.from_env().for_symbol(symbol)
    if not settings.trend_enable:
        return 0.0

    from funtrade.models.components import sector_etf_for

    tail = settings.trend_lookback_days + 280
    df = load_price_bars(symbol, MARKET_ADJ_CLOSE, settings=settings)
    if df.empty:
        return 0.0
    if len(df) > tail:
        df = df.tail(tail)

    price = df["price"].astype(float)
    bench_sym = benchmark_symbol or sector_etf_for(symbol, settings.benchmark)
    bench_df = load_price_bars(bench_sym, MARKET_ADJ_CLOSE, settings=settings)
    bench_price = None
    if not bench_df.empty:
        if len(bench_df) > tail:
            bench_df = bench_df.tail(tail)
        bench_price = bench_df["price"].astype(float).reindex(price.index, method="ffill")

    z_trend = _compute_z_trend(
        price,
        bench_price,
        lookback=settings.trend_lookback_days,
        use_benchmark=settings.trend_use_benchmark,
    )
    if z_trend.empty:
        return 0.0
    return float(z_trend.iloc[-1])


def _compute_regime_validity(
    magnitude: pd.Series,
    price: pd.Series,
    volume: pd.Series,
    spike_sigma: float,
    consecutive_bars: int,
    min_daily_volume_eur: float,
) -> pd.Series:
    spike_mask = magnitude > spike_sigma
    consecutive_spikes = spike_mask.astype(int).rolling(consecutive_bars).sum() >= consecutive_bars

    vol = volume.fillna(0.0)
    # NAV-priced mutual funds often report zero volume; skip liquidity gate when absent.
    if min_daily_volume_eur > 0 and (vol > 0).any():
        eur_volume = (price * vol).rolling(20, min_periods=5).mean()
        liquidity_halt = eur_volume < min_daily_volume_eur
    else:
        liquidity_halt = pd.Series(False, index=magnitude.index)

    valid = ~(consecutive_spikes | liquidity_halt)
    return valid.fillna(True)


def detect_latest_perturbations(
    symbols: list[str] | None = None,
    *,
    settings: Settings | None = None,
    persist: bool = True,
) -> list[PerturbationResult]:
    from funtrade.models.regime_router import compute_regime_series

    settings = settings or Settings.from_env()
    symbols = symbols or settings.watchlist
    results: list[PerturbationResult] = []

    for symbol in symbols:
        try:
            sym_settings = settings.for_symbol(symbol)
            series = compute_perturbation_series(
                symbol,
                weights=sym_settings.perturbation_weights(),
                settings=sym_settings,
            )
            if series.empty:
                continue

            regime_df = compute_regime_series(
                symbol, settings=sym_settings, perturbation=series,
            )
            if not regime_df.empty:
                series = series.join(regime_df[["market_regime", "selected_model"]], how="left")

            latest = series.iloc[-1]
            ts = series.index[-1]
            result = PerturbationResult(
                time=ts,
                symbol=symbol,
                asset_class=sym_settings.asset_class or "etf",
                epsilon=float(latest["epsilon"]),
                magnitude=float(latest["magnitude"]),
                regime_valid=bool(latest["regime_valid"]),
                z_return=float(latest["z_return"]),
                z_volume=float(latest["z_volume"]),
                z_rel_strength=float(latest["z_rel_strength"]),
                h1_components={
                    k.replace("h1_", ""): float(latest[k])
                    for k in latest.index
                    if str(k).startswith("h1_")
                },
                inputs={
                    "z_return": float(latest["z_return"]),
                    "z_volume": float(latest["z_volume"]),
                    "z_rel_strength": float(latest["z_rel_strength"]),
                    "z_vol": float(latest["z_vol"]),
                    "z_trend": float(latest.get("z_trend", 0.0)),
                    "market_regime": str(latest.get("market_regime", "")) or None,
                    "selected_model": str(latest.get("selected_model", "")) or None,
                    "price": float(latest["price"]),
                    "h1": {
                        k.replace("h1_", ""): float(latest[k])
                        for k in latest.index
                        if str(k).startswith("h1_")
                    },
                },
            )
            results.append(result)

            if persist:
                upsert_perturbation_daily(symbol, series, settings=sym_settings)
                save_perturbation_event(
                    ts.to_pydatetime(),
                    symbol,
                    result.magnitude,
                    result.epsilon,
                    result.inputs,
                    result.regime_valid,
                    settings=sym_settings,
                )
        except Exception:
            continue

    return results


def signal_from_epsilon(
    epsilon: float,
    threshold: float,
    regime_valid: bool,
    *,
    long_only: bool = True,
    current_position: float = 0.0,
    z_trend: float = 0.0,
    trend_gate_sells: bool = False,
    trend_gate_z: float = 0.5,
) -> int:
    """Return +1 (buy), -1 (sell/exit), or 0 (flat). Mean-reversion on perturbation."""
    if abs(epsilon) <= threshold:
        return 0

    if epsilon > threshold:
        if long_only:
            if current_position > 0:
                if trend_gate_sells and z_trend > trend_gate_z:
                    return 0
                return -1
            return 0
        if not regime_valid:
            return 0
        return -1

    # epsilon < -threshold: buy / add long only when regime is valid.
    if not regime_valid:
        return 0
    return 1


def trend_signal_kwargs(settings: Settings, z_trend: float = 0.0) -> dict:
    """Extra kwargs for signal_from_epsilon when trend expectation is enabled."""
    if not settings.trend_enable:
        return {}
    return {
        "z_trend": z_trend,
        "trend_gate_sells": settings.trend_gate_sells,
        "trend_gate_z": settings.trend_gate_z,
    }
