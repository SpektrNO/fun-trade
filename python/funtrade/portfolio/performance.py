"""Portfolio performance vs a base date (price % change and PnL)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from funtrade.config import Settings
from funtrade.data.loader import MARKET_ADJ_CLOSE, load_price_bars_batch


@dataclass(frozen=True)
class HoldingPerformance:
    symbol: str
    base_price: float | None
    latest_price: float | None
    base_as_of: date | None
    latest_as_of: date | None
    pct_change: float | None
    pnl: float | None
    arrow: str


@dataclass(frozen=True)
class PortfolioPerformanceResult:
    base_date: date
    currency: str
    rows: pd.DataFrame
    total_pnl: float | None
    total_value_latest: float | None
    total_value_base: float | None
    missing_prices: tuple[str, ...]


def _bar_date(ts: pd.Timestamp) -> date:
    if ts.tzinfo is not None:
        return ts.date()
    return pd.Timestamp(ts).date()


def price_on_or_before(bars: pd.DataFrame, as_of: date) -> tuple[float | None, date | None]:
    """Last close on or before `as_of`. Returns (price, bar_date)."""
    if bars.empty or "price" not in bars.columns:
        return None, None
    idx = bars.index[bars.index.map(_bar_date) <= as_of]
    if len(idx) == 0:
        return None, None
    ts = idx[-1]
    price = float(bars.loc[ts, "price"])
    if pd.isna(price) or price <= 0:
        return None, None
    return price, _bar_date(ts)


def latest_price_from_bars(bars: pd.DataFrame) -> tuple[float | None, date | None]:
    if bars.empty or "price" not in bars.columns:
        return None, None
    ts = bars.index[-1]
    price = float(bars.loc[ts, "price"])
    if pd.isna(price) or price <= 0:
        return None, None
    return price, _bar_date(ts)


def pct_change(base: float, latest: float) -> float:
    return (latest / base - 1.0) * 100.0


def change_arrow(pct: float | None, *, flat_tol_pct: float = 0.05) -> str:
    """Unicode arrow for display: up / down / flat."""
    if pct is None:
        return "—"
    if abs(pct) < flat_tol_pct:
        return "→"
    if pct > 0:
        return "▲"
    return "▼"


def format_change_cell(pct: float | None, arrow: str) -> str:
    if pct is None:
        return "—"
    sign = "+" if pct > 0 else ""
    return f"{arrow} {sign}{pct:.2f}%"


def holding_pnl(
    *,
    shares: float | None,
    value_latest: float | None,
    base_price: float,
    latest_price: float,
) -> float | None:
    """PnL in portfolio currency for one holding.

    Prefer scaling current marked value by the price ratio (stays in portfolio
    currency). Fall back to shares × Δprice when no value is stored.
    """
    if value_latest is not None and value_latest > 0 and latest_price > 0:
        value_base = float(value_latest) * (base_price / latest_price)
        return float(value_latest) - value_base
    if shares is not None and shares > 0:
        return float(shares) * (latest_price - base_price)
    return None


def default_base_date(*, today: date | None = None) -> date:
    """Previous calendar month start-ish: 30 days ago (clamped later by data)."""
    today = today or date.today()
    return today - timedelta(days=30)


def compute_portfolio_performance(
    holdings: pd.DataFrame,
    *,
    base_date: date,
    currency: str,
    settings: Settings | None = None,
) -> PortfolioPerformanceResult:
    """Compare latest prices to base-date prices for portfolio holdings."""
    settings = settings or Settings.from_env()
    if holdings.empty or "symbol" not in holdings.columns:
        return PortfolioPerformanceResult(
            base_date=base_date,
            currency=currency,
            rows=pd.DataFrame(),
            total_pnl=None,
            total_value_latest=None,
            total_value_base=None,
            missing_prices=(),
        )

    symbols = [str(s) for s in holdings["symbol"].tolist()]
    bars_by_symbol = load_price_bars_batch(symbols, market=MARKET_ADJ_CLOSE, settings=settings)

    perf_rows: list[dict] = []
    missing: list[str] = []
    total_pnl = 0.0
    pnl_count = 0
    total_latest = 0.0
    total_base = 0.0
    value_count = 0

    for _, row in holdings.iterrows():
        symbol = str(row["symbol"])
        bars = bars_by_symbol.get(symbol, pd.DataFrame())
        base_price, base_as_of = price_on_or_before(bars, base_date)
        latest_price, latest_as_of = latest_price_from_bars(bars)

        pct: float | None = None
        pnl: float | None = None
        if base_price is not None and latest_price is not None:
            pct = pct_change(base_price, latest_price)
            shares = row["shares"] if "shares" in holdings.columns and pd.notna(row.get("shares")) else None
            value_latest = (
                float(row["value_amount"])
                if "value_amount" in holdings.columns and pd.notna(row.get("value_amount"))
                else None
            )
            pnl = holding_pnl(
                shares=float(shares) if shares is not None else None,
                value_latest=value_latest,
                base_price=base_price,
                latest_price=latest_price,
            )
            if pnl is not None:
                total_pnl += pnl
                pnl_count += 1
            if value_latest is not None and value_latest > 0 and latest_price > 0:
                value_base = value_latest * (base_price / latest_price)
                total_latest += value_latest
                total_base += value_base
                value_count += 1
        else:
            missing.append(symbol)

        arrow = change_arrow(pct)
        perf_rows.append(
            {
                "symbol": symbol,
                "base_price": base_price,
                "latest_price": latest_price,
                "base_as_of": base_as_of.isoformat() if base_as_of else None,
                "latest_as_of": latest_as_of.isoformat() if latest_as_of else None,
                "pct_change": round(pct, 2) if pct is not None else None,
                "pnl": round(pnl, 2) if pnl is not None else None,
                "change": format_change_cell(pct, arrow),
                "arrow": arrow,
            }
        )

    return PortfolioPerformanceResult(
        base_date=base_date,
        currency=currency,
        rows=pd.DataFrame(perf_rows),
        total_pnl=round(total_pnl, 2) if pnl_count else None,
        total_value_latest=round(total_latest, 2) if value_count else None,
        total_value_base=round(total_base, 2) if value_count else None,
        missing_prices=tuple(missing),
    )
