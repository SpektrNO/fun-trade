"""Component variable roles for perturbation-theory fund model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ComponentRole(str, Enum):
    H0 = "h0"
    H1 = "h1"


@dataclass(frozen=True)
class ComponentVariable:
    id: str
    name: str
    role: ComponentRole
    description: str
    unit: str


H0_COMPONENTS: tuple[ComponentVariable, ...] = (
    ComponentVariable(
        "eur_rates",
        "Euro Rates Proxy",
        ComponentRole.H0,
        "Discount-rate anchor via bond ETF or yield proxy.",
        "ratio",
    ),
    ComponentVariable(
        "credit_spread",
        "Credit Spread",
        ComponentRole.H0,
        "HYG-LQD or EU credit pair spread.",
        "ratio",
    ),
    ComponentVariable(
        "eur_usd",
        "EUR/USD",
        ComponentRole.H0,
        "FX headwind for US-heavy funds.",
        "ratio",
    ),
    ComponentVariable(
        "sector_beta",
        "Sector Beta Residual",
        ComponentRole.H0,
        "Rolling OLS residual vs benchmark ETF.",
        "ratio",
    ),
)

H1_COMPONENTS: tuple[ComponentVariable, ...] = (
    ComponentVariable(
        "z_return",
        "Return vs Equilibrium",
        ComponentRole.H1,
        "OU residual normalized by sigma.",
        "z_score",
    ),
    ComponentVariable(
        "z_volume",
        "Volume Stress",
        ComponentRole.H1,
        "Volume vs 20d median.",
        "z_score",
    ),
    ComponentVariable(
        "z_rel_strength",
        "Relative Strength",
        ComponentRole.H1,
        "Symbol return minus sector ETF return.",
        "z_score",
    ),
    ComponentVariable(
        "z_vol",
        "Volatility Spike",
        ComponentRole.H1,
        "20d realized vol vs 252d baseline.",
        "z_score",
    ),
)

ALL_COMPONENTS = {c.id: c for c in (*H0_COMPONENTS, *H1_COMPONENTS)}

# Per-symbol sector/benchmark ETF for relative strength
SECTOR_ETF_MAP: dict[str, str] = {
    "VWCE.DE": "EXSA.DE",
    "EUNL.DE": "EXSA.DE",
    "IS3N.DE": "EXSA.DE",
    "SXR8.DE": "EXSA.DE",
    "AGGH.DE": "IBCI.DE",
    "IBCI.DE": "EXSA.DE",
}

DEFAULT_H1_WEIGHTS: dict[str, float] = {
    "z_return": 0.35,
    "z_volume": 0.10,
    "z_rel_strength": 0.25,
    "z_vol": 0.15,
    "credit_spread": 0.05,
    "eur_usd": 0.05,
    "sector_beta": 0.05,
}


def sector_etf_for(symbol: str, benchmark: str) -> str:
    return SECTOR_ETF_MAP.get(symbol, benchmark)
