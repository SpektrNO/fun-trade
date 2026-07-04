"""Jacobian sensitivity analysis for perturbation drivers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from funtrade.models.equilibrium import EquilibriumModel
from funtrade.models.perturbation import compute_perturbation_series


@dataclass
class SensitivityResult:
    symbol: str
    jacobian: dict[str, float]
    ranked_drivers: list[tuple[str, float]]
    suggested_weights: tuple[float, float, float]


def _epsilon_at_point(
    z_return: float,
    z_volume: float,
    z_rel_strength: float,
    weights: tuple[float, float, float],
) -> float:
    w1, w2, w3 = weights
    return w1 * z_return + w2 * z_volume + w3 * z_rel_strength


def compute_jacobian(
    symbol: str,
    *,
    equilibrium: EquilibriumModel | None = None,
    weights: tuple[float, float, float] = (0.35, 0.10, 0.25),
    delta: float = 0.01,
) -> SensitivityResult:
    series = compute_perturbation_series(symbol, weights=weights, equilibrium=equilibrium)
    if series.empty:
        raise ValueError(f"No data for Jacobian analysis in symbol {symbol}")

    latest = series.iloc[-1]
    base = _epsilon_at_point(
        float(latest["z_return"]),
        float(latest["z_volume"]),
        float(latest["z_rel_strength"]),
        weights,
    )

    jacobian = {
        "z_return": (
            _epsilon_at_point(
                float(latest["z_return"]) + delta,
                float(latest["z_volume"]),
                float(latest["z_rel_strength"]),
                weights,
            )
            - base
        )
        / delta,
        "z_volume": (
            _epsilon_at_point(
                float(latest["z_return"]),
                float(latest["z_volume"]) + delta,
                float(latest["z_rel_strength"]),
                weights,
            )
            - base
        )
        / delta,
        "z_rel_strength": (
            _epsilon_at_point(
                float(latest["z_return"]),
                float(latest["z_volume"]),
                float(latest["z_rel_strength"]) + delta,
                weights,
            )
            - base
        )
        / delta,
    }

    empirical = _empirical_sensitivities(series)
    for key, value in empirical.items():
        jacobian[f"empirical_{key}"] = value

    ranked = sorted(
        [(k, abs(v)) for k, v in jacobian.items() if not k.startswith("empirical_")],
        key=lambda x: x[1],
        reverse=True,
    )

    total = sum(abs(v) for _, v in ranked) or 1.0
    w_return = next(v for k, v in ranked if k == "z_return") / total
    w_volume = next(v for k, v in ranked if k == "z_volume") / total
    w_rs = next(v for k, v in ranked if k == "z_rel_strength") / total

    return SensitivityResult(
        symbol=symbol,
        jacobian=jacobian,
        ranked_drivers=ranked,
        suggested_weights=(w_return, w_volume, w_rs),
    )


def _empirical_sensitivities(series: pd.DataFrame) -> dict[str, float]:
    result = {}
    target = series["magnitude"].astype(float)
    for col in ["z_return", "z_volume", "z_rel_strength"]:
        driver = series[col].astype(float)
        if driver.std() < 1e-6:
            result[col] = 0.0
            continue
        corr = target.corr(driver)
        result[col] = float(corr) if not np.isnan(corr) else 0.0
    return result


def tune_weights_from_jacobian(symbol: str) -> tuple[float, float, float]:
    result = compute_jacobian(symbol)
    return result.suggested_weights
