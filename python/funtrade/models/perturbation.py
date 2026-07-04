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


@dataclass
class PerturbationResult:
    time: pd.Timestamp
    symbol: str
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


def compute_perturbation_series(
    symbol: str,
    *,
    benchmark_symbol: str | None = None,
    weights: tuple[float, float, float] = (0.35, 0.10, 0.25),
    settings: Settings | None = None,
    equilibrium: EquilibriumModel | None = None,
) -> pd.DataFrame:
    settings = settings or Settings.from_env()
    equilibrium = equilibrium or load_or_calibrate(symbol)

    df = load_price_bars(symbol, MARKET_ADJ_CLOSE, settings=settings)
    if df.empty:
        return pd.DataFrame()

    price = df["price"].astype(float)
    band = equilibrium.equilibrium_band(price, symbol=symbol)
    z_return = band["residual"] / equilibrium.sigma

    volume = df["volume"].astype(float) if "volume" in df.columns else pd.Series(0.0, index=df.index)
    if volume.notna().sum() > 5:
        z_volume = _zscore(volume.fillna(volume.median()))
    else:
        z_volume = pd.Series(0.0, index=df.index)

    bench_sym = benchmark_symbol or sector_etf_for(symbol, settings.benchmark)
    bench_df = load_price_bars(bench_sym, MARKET_ADJ_CLOSE, settings=settings)
    if not bench_df.empty:
        bench_price = bench_df["price"].astype(float).reindex(price.index, method="ffill")
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
        "regime_valid": regime_valid,
        "price": price,
    }
    for col in h1_scores.columns:
        out[f"h1_{col}"] = h1_scores[col]
    return pd.DataFrame(out, index=df.index)


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
    settings = settings or Settings.from_env()
    symbols = symbols or settings.watchlist
    results: list[PerturbationResult] = []

    for symbol in symbols:
        try:
            series = compute_perturbation_series(symbol, settings=settings)
            if series.empty:
                continue

            latest = series.iloc[-1]
            ts = series.index[-1]
            result = PerturbationResult(
                time=ts,
                symbol=symbol,
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
                upsert_perturbation_daily(symbol, series, settings=settings)
                save_perturbation_event(
                    ts.to_pydatetime(),
                    symbol,
                    result.magnitude,
                    result.epsilon,
                    result.inputs,
                    result.regime_valid,
                    settings=settings,
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
) -> int:
    """Return +1 (buy), -1 (sell/exit), or 0 (flat). Mean-reversion on perturbation."""
    if abs(epsilon) <= threshold:
        return 0

    if epsilon > threshold:
        if long_only:
            # Exit long when overvalued — allowed even if regime invalid (de-risk).
            return -1 if current_position > 0 else 0
        if not regime_valid:
            return 0
        return -1

    # epsilon < -threshold: buy / add long only when regime is valid.
    if not regime_valid:
        return 0
    return 1
