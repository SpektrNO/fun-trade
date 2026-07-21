"""Latest mutual-fund NAV from Nordnet fund pages (ahead of Yahoo .IR tickers)."""

from __future__ import annotations

import json
import re

import pandas as pd
import requests

from funtrade.data.loader import normalize_daily_bars
from funtrade.portfolio.nordnet_profiles import _DEFAULT_HEADERS, nordnet_fund_url
from funtrade.portfolio.profile_fetch import load_nordnet_slugs


def _parse_latest_nav(html: str) -> tuple[str, float] | None:
    match = re.search(r'window\.__initialProps__="(\{.*?\})";', html)
    if not match:
        return None
    raw = match.group(1).encode("utf-8").decode("unicode_escape")
    data = json.loads(raw)
    latest = (
        data.get("initialProps", {})
        .get("sharedInitialProps", {})
        .get("initialFundData", {})
        .get("navInfo", {})
        .get("latestNav")
    )
    if not isinstance(latest, dict):
        return None
    date = latest.get("date")
    value = latest.get("value")
    if not date or value is None:
        return None
    nav = float(value)
    if nav <= 0 or pd.isna(nav):
        return None
    return str(date), nav


def fetch_latest_nordnet_nav(symbol: str, *, slug: str | None = None) -> tuple[str, float] | None:
    """Return (YYYY-MM-DD, NAV) for a watchlist symbol with a Nordnet fund slug."""
    slug = slug or load_nordnet_slugs().get(symbol)
    if not slug:
        return None
    url = nordnet_fund_url(slug)
    if "/etf/" in url:
        return None
    resp = requests.get(url, headers=_DEFAULT_HEADERS, timeout=30)
    resp.raise_for_status()
    return _parse_latest_nav(resp.text)


def merge_nordnet_nav_bar(bars: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Append or update the latest Nordnet NAV when newer than ingested bars."""
    nav = fetch_latest_nordnet_nav(symbol)
    if nav is None:
        return bars

    date_str, nav_val = nav
    idx = pd.DatetimeIndex([date_str], tz="UTC")
    nav_bar = pd.DataFrame(
        [{"open": nav_val, "high": nav_val, "low": nav_val, "close": nav_val, "volume": 0.0}],
        index=idx,
    )
    nav_bar = normalize_daily_bars(nav_bar)
    nav_date = nav_bar.index[-1]
    nav_row = nav_bar.iloc[-1]
    close = float(nav_row["close"])

    base = normalize_daily_bars(bars.copy()) if not bars.empty else pd.DataFrame()
    if base.empty:
        return nav_bar
    if nav_date < base.index[-1]:
        return bars
    if nav_date == base.index[-1]:
        existing_close = float(base.loc[nav_date, "close"])
        if abs(existing_close - close) < 1e-9:
            return bars
        updated = base.copy()
        row = updated.loc[nav_date].copy()
        row["close"] = close
        row["open"] = close
        row["high"] = close
        row["low"] = close
        row["volume"] = 0.0
        updated.loc[nav_date] = row
        return updated
    return pd.concat([base, nav_bar]).sort_index()
