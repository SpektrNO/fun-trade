"""Seed synthetic daily ETF bars for local development."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from funtrade.config import Settings, get_connection
from funtrade.data.loader import MARKET_ADJ_CLOSE, upsert_price_bars


def generate_synthetic_prices(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range(start, end, freq="D", tz="UTC")
    n = len(index)

    kappa = 0.05
    mu = 3.5
    sigma = 0.08
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = x[i - 1] + kappa * (mu - x[i - 1]) + sigma * rng.normal()

    dow = index.dayofweek
    season = 0.05 * np.sin(2 * np.pi * dow / 5)
    log_prices = x + season
    prices = np.exp(log_prices)
    volume = rng.integers(10000, 500000, size=n).astype(float)

    return pd.DataFrame(
        {
            "open": prices * 0.999,
            "high": prices * 1.002,
            "low": prices * 0.998,
            "close": prices,
            "volume": volume,
        },
        index=index,
    )


def seed_demo_data(symbols: list[str] | None = None, days: int = 800) -> int:
    settings = Settings.from_env()
    symbols = symbols or settings.watchlist
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    total = 0

    for symbol in symbols:
        df = generate_synthetic_prices(symbol, start, end, seed=hash(symbol) % 10000)
        total += upsert_price_bars(symbol, df, market=MARKET_ADJ_CLOSE, source="seed", settings=settings)

    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo price data")
    parser.add_argument("--days", type=int, default=800)
    parser.add_argument("--symbol", default=None)
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else None
    count = seed_demo_data(symbols=symbols, days=args.days)
    print(f"Seeded {count} price bars")


if __name__ == "__main__":
    main()
