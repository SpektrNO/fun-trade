"""Cross-validate Stooq vs EOD daily close prices."""

from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd

from funtrade.config import Settings, get_connection, read_sql_df
from funtrade.data.eod import EodPriceProvider
from funtrade.data.stooq import StooqPriceProvider


@dataclass
class ReconcileReport:
    symbol: str
    matched_days: int
    mean_abs_diff_bps: float
    max_diff_bps: float
    agreement_rate: float
    outliers: int


def reconcile_symbol(
    symbol: str,
    *,
    diff_threshold_bps: float | None = None,
    days: int = 365,
    settings: Settings | None = None,
    persist: bool = True,
) -> ReconcileReport:
    settings = settings or Settings.from_env()
    threshold = float(
        diff_threshold_bps
        if diff_threshold_bps is not None
        else os.getenv("RECONCILE_DIFF_THRESHOLD_BPS", "10")
    )

    stooq_db = read_sql_df(
        """
        SELECT time, price AS stooq_price
        FROM price_bars
        WHERE symbol = %(symbol)s AND market = 'adj_close' AND source = 'stooq'
        ORDER BY time
        """,
        {"symbol": symbol},
        settings=settings,
    )

    end = pd.Timestamp.now(tz="UTC").normalize()
    start = end - pd.Timedelta(days=days)

    if stooq_db.empty:
        provider = StooqPriceProvider()
        bars = provider.fetch_bars(symbol, start, end)
        if bars.empty:
            return ReconcileReport(symbol, 0, 0.0, 0.0, 0.0, 0)
        stooq = pd.DataFrame({"time": bars.index, "stooq_price": bars["close"].values})
    else:
        stooq = stooq_db.copy()
        stooq["time"] = pd.to_datetime(stooq["time"], utc=True)

    try:
        eod_provider = EodPriceProvider()
        eod_bars = eod_provider.fetch_bars(symbol, start, end)
        if eod_bars.empty:
            return ReconcileReport(symbol, 0, 0.0, 0.0, 0.0, 0)
        eod = pd.DataFrame({"time": eod_bars.index, "eod_price": eod_bars["close"].values})
    except ValueError:
        return ReconcileReport(symbol, 0, 0.0, 0.0, 0.0, 0)

    merged = stooq.merge(eod, on="time", how="inner")
    if merged.empty:
        return ReconcileReport(symbol, 0, 0.0, 0.0, 0.0, 0)

    merged["diff_bps"] = (
        (merged["stooq_price"] - merged["eod_price"]).abs()
        / merged["stooq_price"].clip(lower=0.01)
        * 10000
    )
    outliers = int((merged["diff_bps"] > threshold).sum())
    agreement = 1.0 - (outliers / len(merged)) if len(merged) else 0.0

    if persist:
        with get_connection(settings) as conn:
            with conn.cursor() as cur:
                for row in merged.itertuples(index=False):
                    cur.execute(
                        """
                        INSERT INTO data_quality_checks
                            (symbol, time, provider_a, provider_b, price_a, price_b, diff_bps)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            symbol,
                            row.time.to_pydatetime(),
                            "stooq",
                            "eod",
                            float(row.stooq_price),
                            float(row.eod_price),
                            float(row.diff_bps),
                        ),
                    )
            conn.commit()

    return ReconcileReport(
        symbol=symbol,
        matched_days=len(merged),
        mean_abs_diff_bps=float(merged["diff_bps"].mean()),
        max_diff_bps=float(merged["diff_bps"].max()),
        agreement_rate=float(agreement),
        outliers=outliers,
    )
