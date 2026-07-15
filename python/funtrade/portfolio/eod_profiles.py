"""Fetch ETF composition from EOD Historical Data fundamentals API."""

from __future__ import annotations

import os
from typing import Any

import requests

from funtrade.portfolio.fund_profiles import FundProfile

_EOD_BASE = "https://eodhistoricaldata.com/api/fundamentals"

# EOD sector names → FunTrade fund_profiles naming
_SECTOR_MAP: dict[str, str] = {
    "Basic Materials": "Materials",
    "Consumer Cyclicals": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Financial Services": "Financials",
    "Real Estate": "Real Estate",
    "Communication Services": "Communication Services",
    "Energy": "Energy",
    "Industrials": "Industrials",
    "Technology": "Technology",
    "Healthcare": "Healthcare",
    "Utilities": "Utilities",
}

_REGION_MAP: dict[str, str] = {
    "North America": "North America",
    "Europe Developed": "Europe Developed",
    "United Kingdom": "Europe Developed",
    "Europe Emerging": "Emerging Markets",
    "Asia Developed": "Asia Pacific Developed",
    "Asia Emerging": "Emerging Markets",
    "Latin America": "Emerging Markets",
    "Africa/Middle East": "Emerging Markets",
    "Japan": "Asia Pacific Developed",
    "Australasia": "Asia Pacific Developed",
}


def eod_ticker_for(symbol: str) -> str:
    """Map watchlist symbol to EOD fundamentals ticker."""
    sym = symbol.strip()
    upper = sym.upper()
    if upper.endswith(".DE"):
        return f"{sym[:-3]}.XETRA"
    if upper.endswith(".AS"):
        return sym
    if upper.endswith(".SW"):
        return sym
    if upper.endswith(".L") or upper.endswith(".LSE"):
        return sym
    if "." not in sym:
        return f"{sym}.US"
    return sym


def _pct_weights(raw: dict[str, Any] | None, *, field: str = "Equity_%") -> dict[str, float]:
    if not raw:
        return {}
    out: dict[str, float] = {}
    for name, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        val = payload.get(field) or payload.get("Net_Assets_%") or payload.get("Long_%")
        if val is None:
            continue
        weight = float(val) / 100.0
        if weight > 0:
            out[str(name)] = weight
    return out


def _normalize_weights(weights: dict[str, float], mapping: dict[str, str]) -> dict[str, float]:
    merged: dict[str, float] = {}
    for name, weight in weights.items():
        key = mapping.get(name, name)
        merged[key] = merged.get(key, 0.0) + weight
    total = sum(merged.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in merged.items()}


def _asset_classes_from_allocation(raw: dict[str, Any] | None) -> dict[str, float]:
    if not raw:
        return {}
    equity = 0.0
    fixed_income = 0.0
    cash = 0.0
    other = 0.0
    for name, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        net = payload.get("Net_Assets_%") or payload.get("Long_%")
        if net is None:
            continue
        w = float(net) / 100.0
        label = str(name).lower()
        if "stock" in label or "equity" in label:
            equity += w
        elif "bond" in label or "fixed" in label:
            fixed_income += w
        elif "cash" in label:
            cash += w
        else:
            other += w
    out: dict[str, float] = {}
    if equity > 0:
        out["Equity"] = equity
    if fixed_income > 0:
        out["Fixed Income"] = fixed_income
    if cash > 0:
        out["Cash"] = cash
    if other > 0:
        out["Other"] = other
    total = sum(out.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in out.items()}


def parse_eod_etf_profile(symbol: str, payload: dict[str, Any]) -> FundProfile:
    """Build FundProfile from EOD fundamentals JSON (ETF)."""
    general = payload.get("General") or {}
    etf = payload.get("ETF_Data") or {}
    name = str(general.get("Name") or etf.get("Company_Name") or symbol)
    as_of = str(etf.get("UpdatedAt") or general.get("UpdatedAt") or "unknown")[:10]

    regions = _normalize_weights(
        _pct_weights(etf.get("World_Regions")),
        _REGION_MAP,
    )
    sectors = _normalize_weights(
        _pct_weights(etf.get("Sector_Weights")),
        _SECTOR_MAP,
    )
    asset_classes = _asset_classes_from_allocation(etf.get("Asset_Allocation"))

    if not asset_classes and regions:
        asset_classes = {"Equity": 0.99, "Cash": 0.01}

    return FundProfile(
        symbol=symbol,
        name=name,
        as_of=as_of,
        source="EOD Historical Data (ETF fundamentals)",
        regions=regions,
        sectors=sectors,
        asset_classes=asset_classes,
    )


def fetch_eod_etf_profile(
    symbol: str,
    *,
    api_token: str | None = None,
    session: requests.Session | None = None,
) -> FundProfile:
    """Download and parse ETF fundamentals from EOD."""
    token = api_token or os.getenv("EOD_API_TOKEN", "").strip()
    if not token:
        raise ValueError("EOD_API_TOKEN required to fetch ETF profiles")

    eod_symbol = eod_ticker_for(symbol)
    url = f"{_EOD_BASE}/{eod_symbol}?api_token={token}&fmt=json"
    sess = session or requests
    resp = sess.get(url, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict) or not payload.get("ETF_Data"):
        raise ValueError(f"No ETF_Data in EOD response for {symbol} ({eod_symbol})")
    return parse_eod_etf_profile(symbol, payload)
