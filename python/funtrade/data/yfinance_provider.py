"""yfinance daily price adapter (fallback when Stooq blocks automated access)."""

from __future__ import annotations

from typing import Literal

import pandas as pd
import yfinance as yf

from funtrade.data.symbols import resolve_fetch_ticker


class YFinancePriceProvider:
    def fetch_bars(
        self,
        symbol: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
        interval: Literal["1d"] = "1d",
    ) -> pd.DataFrame:
        if interval != "1d":
            raise ValueError("yfinance adapter only supports daily bars")

        ticker = yf.Ticker(resolve_fetch_ticker(symbol))
        df = ticker.history(start=start.tz_localize(None), end=end.tz_localize(None), auto_adjust=True)
        if df.empty:
            return pd.DataFrame()

        df.index = pd.DatetimeIndex(df.index)
        df = df.rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        return df[["open", "high", "low", "close", "volume"]].astype(float, errors="ignore")
