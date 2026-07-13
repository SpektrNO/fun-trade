"""Look-through portfolio allocation from portfolio.json + fund_profiles."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from funtrade.portfolio.fund_profiles import FundProfile, load_fund_profile
from funtrade.portfolio_config import PortfolioConfig, load_portfolio_config


@dataclass(frozen=True)
class PortfolioAllocationResult:
    name: str
    currency: str
    valuation_mode: str
    total_weight_pct: float
    weight_is_normalized: bool
    holdings: pd.DataFrame
    regions: pd.DataFrame
    sectors: pd.DataFrame
    asset_classes: pd.DataFrame
    missing_profiles: tuple[str, ...]
    uncovered_weight_pct: float


def _holding_weights(portfolio: PortfolioConfig) -> dict[str, float]:
    if portfolio.valuation_mode == "weight_pct":
        raw = {h.symbol: float(h.weight_pct or 0.0) for h in portfolio.holdings}
    elif portfolio.valuation_mode == "value_eur":
        raw = {h.symbol: float(h.value_eur or 0.0) for h in portfolio.holdings}
    else:
        raise NotImplementedError(
            f"Portfolio allocation for valuation_mode={portfolio.valuation_mode!r} not implemented yet"
        )
    total = sum(raw.values())
    if total <= 0:
        return {}
    return {sym: w / total for sym, w in raw.items()}


def _rollup(
    weights: dict[str, float],
    profiles: dict[str, FundProfile],
    bucket: str,
) -> pd.DataFrame:
    totals: dict[str, float] = {}
    for symbol, port_w in weights.items():
        profile = profiles.get(symbol)
        if profile is None:
            continue
        parts: dict[str, float] = getattr(profile, bucket)
        for category, frac in parts.items():
            totals[category] = totals.get(category, 0.0) + port_w * frac
    if not totals:
        return pd.DataFrame(columns=["category", "weight_pct"])
    rows = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    return pd.DataFrame(
        {
            "category": [name for name, _ in rows],
            "weight_pct": [w * 100.0 for _, w in rows],
        }
    )


def compute_portfolio_allocation(
    portfolio: PortfolioConfig | None = None,
) -> PortfolioAllocationResult | None:
    portfolio = portfolio if portfolio is not None else load_portfolio_config()
    if portfolio is None or not portfolio.holdings:
        return None

    weights = _holding_weights(portfolio)
    profiles: dict[str, FundProfile] = {}
    missing: list[str] = []
    for symbol in portfolio.symbols():
        prof = load_fund_profile(symbol)
        if prof is None:
            missing.append(symbol)
        else:
            profiles[symbol] = prof

    raw_total = portfolio.total_weight_pct()
    normalized = portfolio.valuation_mode == "weight_pct" and abs(raw_total - 100.0) > 0.01

    holding_rows: list[dict] = []
    for h in portfolio.holdings:
        prof = profiles.get(h.symbol)
        port_w = weights.get(h.symbol, 0.0)
        holding_rows.append(
            {
                "symbol": h.symbol,
                "weight_pct": h.weight_pct,
                "value_eur": h.value_eur,
                "shares": h.shares,
                "portfolio_weight_pct": round(port_w * 100.0, 2),
                "name": prof.name if prof else "",
                "profile_as_of": prof.as_of if prof else "",
                "has_profile": prof is not None,
                "note": h.note or "",
            }
        )

    uncovered = sum(w for sym, w in weights.items() if sym in missing) * 100.0

    return PortfolioAllocationResult(
        name=portfolio.name,
        currency=portfolio.currency,
        valuation_mode=portfolio.valuation_mode,
        total_weight_pct=raw_total,
        weight_is_normalized=normalized,
        holdings=pd.DataFrame(holding_rows),
        regions=_rollup(weights, profiles, "regions"),
        sectors=_rollup(weights, profiles, "sectors"),
        asset_classes=_rollup(weights, profiles, "asset_classes"),
        missing_profiles=tuple(missing),
        uncovered_weight_pct=uncovered,
    )
