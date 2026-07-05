"""Latest market price lookup."""

from __future__ import annotations

from funtrade.config import Settings
from funtrade.data.loader import MARKET_ADJ_CLOSE, load_price_bars


def latest_price(symbol: str, *, settings: Settings | None = None) -> float | None:
    df = load_price_bars(symbol, MARKET_ADJ_CLOSE, settings=settings)
    if df.empty:
        return None
    return float(df["price"].iloc[-1])
