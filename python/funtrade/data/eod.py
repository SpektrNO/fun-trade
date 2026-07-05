"""EOD Historical Data adapter (optional reconcile source)."""

from __future__ import annotations

import os
from typing import Literal

import pandas as pd
import requests


class EodPriceProvider:
    def __init__(self, api_token: str | None = None) -> None:
        self.api_token = api_token or os.getenv("EOD_API_TOKEN", "")
        if not self.api_token:
            raise ValueError("EOD_API_TOKEN required for EOD adapter")

    def fetch_bars(
        self,
        symbol: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
        interval: Literal["1d"] = "1d",
    ) -> pd.DataFrame:
        if interval != "1d":
            raise ValueError("EOD adapter only supports daily bars")

        eod_symbol = symbol.replace(".DE", ".XETRA").replace(".AS", ".AS")
        url = (
            f"https://eodhistoricaldata.com/api/eod/{eod_symbol}"
            f"?api_token={self.api_token}&fmt=json&from={start.date()}&to={end.date()}"
        )
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df = df.set_index("date").sort_index()
        df = df.rename(
            columns={
                "open": "open",
                "high": "high",
                "low": "low",
                "adjusted_close": "close",
                "volume": "volume",
            }
        )
        if "close" not in df.columns and "close" in df.columns:
            pass
        if "close" not in df.columns:
            df["close"] = df.get("adjusted_close", df.get("close"))
        return df[["open", "high", "low", "close", "volume"]].astype(float, errors="ignore")
