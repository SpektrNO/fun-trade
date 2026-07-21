"""Price and factor ingest CLIs."""

from __future__ import annotations

import os

import pandas as pd

from funtrade.config import Settings
from funtrade.data.latest_price import augment_live_eod_bars, primary_has_official_today
from funtrade.data.nordnet_nav import merge_nordnet_nav_bar
from funtrade.data.loader import upsert_price_bars
from funtrade.data.provider import PriceProvider
from funtrade.data.stooq import StooqPriceProvider
from funtrade.data.symbols import has_alias, resolve_fetch_ticker
from funtrade.data.yfinance_provider import YFinancePriceProvider


def _get_provider() -> PriceProvider:
    if os.getenv("STOOQ_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return YFinancePriceProvider()
    try:
        provider = StooqPriceProvider()
        test_end = pd.Timestamp.now(tz="UTC").normalize()
        test_start = test_end - pd.Timedelta(days=7)
        bars = provider.fetch_bars("VWCE.DE", test_start, test_end)
        if not bars.empty:
            return provider
    except Exception:
        pass
    return YFinancePriceProvider()


def _fetch_bars(
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    provider: PriceProvider,
) -> tuple[pd.DataFrame, str]:
    """Fetch daily bars; fall back to yfinance when Stooq has no series."""
    yf_provider = YFinancePriceProvider()

    if has_alias(symbol) and not isinstance(provider, YFinancePriceProvider):
        bars = yf_provider.fetch_bars(symbol, start, end)
        return bars, "yfinance"

    try:
        bars = provider.fetch_bars(symbol, start, end)
    except Exception:
        bars = pd.DataFrame()

    if bars.empty and not isinstance(provider, YFinancePriceProvider):
        bars = yf_provider.fetch_bars(symbol, start, end)
        if not bars.empty:
            return bars, "yfinance"

    source = "stooq" if isinstance(provider, StooqPriceProvider) else "yfinance"
    return bars, source


def ingest_watchlist(
    *,
    days: int = 730,
    symbols: list[str] | None = None,
    settings: Settings | None = None,
) -> dict[str, int]:
    settings = settings or Settings.from_env()
    symbols = symbols or settings.watchlist
    end = pd.Timestamp.now(tz="UTC").normalize()
    start = end - pd.Timedelta(days=days)
    provider = _get_provider()

    counts: dict[str, int] = {}
    for symbol in symbols:
        try:
            bars, source = _fetch_bars(symbol, start, end, provider)
            sym_settings = settings.for_symbol(symbol)
            asset_class = sym_settings.asset_class or "etf"
            if asset_class == "mutual_fund":
                bars = merge_nordnet_nav_bar(bars, symbol)
            elif not primary_has_official_today(bars, source):
                bars = augment_live_eod_bars(bars, symbol, asset_class=asset_class)
            counts[symbol] = upsert_price_bars(symbol, bars, source=source, settings=settings)
        except Exception:
            counts[symbol] = 0
    return counts


def fetch_ticker_for(symbol: str) -> str:
    """Public helper: show which provider ticker is used for a WATCHLIST symbol."""
    return resolve_fetch_ticker(symbol)
