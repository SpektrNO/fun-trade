"""Data provider protocols and adapters."""

from __future__ import annotations

from typing import Literal, Protocol

import pandas as pd


class PriceProvider(Protocol):
    def fetch_bars(
        self,
        symbol: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
        interval: Literal["1d"] = "1d",
    ) -> pd.DataFrame:
        """Return DataFrame with columns: open, high, low, close, volume (index=time UTC)."""
        ...


class FactorProvider(Protocol):
    def fetch_series(
        self,
        series_id: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> pd.Series:
        ...
