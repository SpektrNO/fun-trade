"""Scrape fund/ETF exposure from Nordnet fund pages."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

import requests

from funtrade.portfolio.fund_profiles import FundProfile

NORDNET_FUND_BASE = "https://www.nordnet.no/fond/liste/"
NORDNET_ETF_BASE = "https://www.nordnet.no/etf/liste/"
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; FunTrade/1.0; +https://github.com/spektr/fun-trade)"
    ),
    "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
}

_NORDNET_REGION_MAP: dict[str, str] = {
    "Asia": "Asia",
    "Asia Emerging": "Emerging Markets",
    "Europe Emerging": "Emerging Markets",
    "Latin-Amerika": "Latin America",
    "Midtøsten": "Middle East",
    "Afrika": "Africa",
    "USA": "North America",
    "Eurosonen": "Europe Developed",
    "Europa": "Europe Developed",
    "Norden": "Europe Developed",
    "Storbritannia": "Europe Developed",
    "Japan": "Asia Pacific Developed",
    "Stillehavsregionen": "Asia Pacific Developed",
}

_NORDNET_SECTOR_MAP: dict[str, str] = {
    "Teknologi": "Technology",
    "Finans": "Financials",
    "Konsumentvarer": "Consumer Discretionary",
    "Konsument defensive": "Consumer Staples",
    "Konsumentstapvarer": "Consumer Staples",
    "Industri": "Industrials",
    "Helse": "Healthcare",
    "Energi": "Energy",
    "Materialer": "Materials",
    "Kommunikasjonstjenester": "Communication Services",
    "Utilities": "Utilities",
    "Eiendom": "Real Estate",
}

_NORDNET_ASSET_MAP: dict[str, str] = {
    "Aksjer": "Equity",
    "Kontanter": "Cash",
    "Obligasjoner": "Fixed Income",
    "Renter": "Fixed Income",
    "Øvrig": "Other",
    "Ikke klassifisert": "Other",
}


def nordnet_fund_url(slug_or_url: str) -> str:
    raw = slug_or_url.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    slug = raw.lstrip("/")
    if slug.startswith("etf/liste/"):
        return f"https://www.nordnet.no/{slug}"
    if slug.startswith("fond/liste/"):
        return f"https://www.nordnet.no/{slug}"
    if "-xeta" in slug or "-xnas" in slug or "-xsto" in slug or "-xotc" in slug:
        return f"{NORDNET_ETF_BASE}{slug}"
    return f"{NORDNET_FUND_BASE}{slug}"


def nordnet_slug_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1]


def _extract_escaped_json_array(html: str, key: str) -> list[dict[str, Any]]:
    pattern = rf'\\"{re.escape(key)}\\":(\[.+?\])'
    match = re.search(pattern, html)
    if not match:
        return []
    raw = match.group(1).encode("utf-8").decode("unicode_escape")
    data = json.loads(raw)
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _extract_field(html: str, key: str) -> str | None:
    pattern = rf'\\"{re.escape(key)}\\":\\"([^"\\]+)\\"'
    match = re.search(pattern, html)
    return match.group(1) if match else None


def _weights_from_rows(
    rows: list[dict[str, Any]],
    *,
    mapping: dict[str, str],
) -> dict[str, float]:
    merged: dict[str, float] = {}
    for row in rows:
        name = str(row.get("displayName") or row.get("name") or "").strip()
        weight = row.get("weight")
        if not name or weight is None:
            continue
        key = mapping.get(name, name)
        w = float(weight) / 100.0
        if w <= 0:
            continue
        merged[key] = merged.get(key, 0.0) + w
    total = sum(merged.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in merged.items()}


def parse_nordnet_fund_html(
    html: str,
    *,
    symbol: str,
    slug: str | None = None,
) -> FundProfile:
    """Parse Nordnet fund page HTML (RSC payload with escaped JSON)."""
    regions = _weights_from_rows(
        _extract_escaped_json_array(html, "regions"),
        mapping=_NORDNET_REGION_MAP,
    )
    sectors = _weights_from_rows(
        _extract_escaped_json_array(html, "sectors"),
        mapping=_NORDNET_SECTOR_MAP,
    )
    assets = _weights_from_rows(
        _extract_escaped_json_array(html, "assets"),
        mapping=_NORDNET_ASSET_MAP,
    )

    name = _extract_field(html, "name")
    titles = re.findall(r"<h2[^>]*>([^<]+)</h2>", html)
    h2_name = titles[0].strip() if titles else None
    if h2_name and (not name or len(name) < 8 or name.startswith("App version")):
        name = h2_name
    if not name:
        name = slug or symbol

    as_of = _extract_field(html, "updatedAt") or "unknown"
    source_slug = slug or "nordnet"
    return FundProfile(
        symbol=symbol,
        name=name,
        as_of=as_of[:10],
        source=f"Nordnet ({source_slug})",
        regions=regions,
        sectors=sectors,
        asset_classes=assets,
    )


def fetch_nordnet_fund_profile(
    slug_or_url: str,
    *,
    symbol: str,
    session: requests.Session | None = None,
) -> FundProfile:
    """Fetch and parse a Nordnet fund or ETF profile."""
    url = nordnet_fund_url(slug_or_url)
    slug = nordnet_slug_from_url(url)
    sess = session or requests
    resp = sess.get(url, headers=_DEFAULT_HEADERS, timeout=30)
    resp.raise_for_status()
    return parse_nordnet_fund_html(resp.text, symbol=symbol, slug=slug)
