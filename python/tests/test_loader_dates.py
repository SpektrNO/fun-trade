"""Tests for price bar date normalization."""

from __future__ import annotations

import pandas as pd

from funtrade.data.loader import normalize_daily_bars, trade_date_index


def test_trade_date_index_uses_local_calendar_date():
    # yfinance Xetra: 2026-07-03 00:00 Europe/Berlin must store as 2026-07-03 UTC, not 2026-07-02.
    idx = pd.DatetimeIndex(["2026-07-03 00:00:00+0200"], tz="Europe/Berlin")
    out = trade_date_index(idx)
    assert str(out[0]) == "2026-07-03 00:00:00+00:00"


def test_trade_date_index_dublin_mutual_fund():
    idx = pd.DatetimeIndex(["2026-07-02 00:00:00+0100"], tz="Europe/Dublin")
    out = trade_date_index(idx)
    assert str(out[0]) == "2026-07-02 00:00:00+00:00"


def test_normalize_daily_bars_after_utc_conversion_would_have_shifted():
    # Simulates the old bug path: UTC index for a Jul 3 Berlin bar.
    df = pd.DataFrame({"close": [45.19]}, index=pd.DatetimeIndex(["2026-07-02 22:00:00+00:00"]))
    # Without local tz on index, naive UTC date is kept (Stooq-style dates are fine).
    out = normalize_daily_bars(df)
    assert str(out.index[0]) == "2026-07-02 00:00:00+00:00"


def test_normalize_daily_bars_yfinance_berlin_close():
    df = pd.DataFrame(
        {"close": [45.19], "volume": [1000.0]},
        index=pd.DatetimeIndex(["2026-07-03 00:00:00+0200"], tz="Europe/Berlin"),
    )
    out = normalize_daily_bars(df)
    assert len(out) == 1
    assert str(out.index[0]) == "2026-07-03 00:00:00+00:00"
    assert out["close"].iloc[0] == 45.19
