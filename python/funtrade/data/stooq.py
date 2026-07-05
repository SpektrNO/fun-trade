"""Stooq daily price adapter."""

from __future__ import annotations

import io
from typing import Literal

import pandas as pd
import requests

STOOQ_DAILY_URL = "https://stooq.com/q/d/l/"


def _stooq_symbol(symbol: str) -> str:
    return symbol.lower().replace(".", ".")


class StooqPriceProvider:
    def fetch_bars(
        self,
        symbol: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
        interval: Literal["1d"] = "1d",
    ) -> pd.DataFrame:
        if interval != "1d":
            raise ValueError("Stooq adapter only supports daily bars")

        stooq_sym = _stooq_symbol(symbol)
        url = f"{STOOQ_DAILY_URL}?s={stooq_sym}&i=d"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()

        df = pd.read_csv(io.StringIO(resp.text))
        if df.empty or "Date" not in df.columns:
            return pd.DataFrame()

        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        df = df.loc[(df.index >= start.normalize()) & (df.index <= end.normalize())]

        rename = {
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
        out = df.rename(columns=rename)
        for col in ("open", "high", "low", "close", "volume"):
            if col not in out.columns:
                out[col] = pd.NA
        return out[["open", "high", "low", "close", "volume"]].astype(float, errors="ignore")
