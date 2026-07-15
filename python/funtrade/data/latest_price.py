"""Merge latest exchange quotes into daily bars as a provisional end-of-day close."""

from __future__ import annotations

import pandas as pd
import yfinance as yf

from funtrade.data.loader import normalize_daily_bars, trade_date_index
from funtrade.data.symbols import resolve_fetch_ticker
from funtrade.universe_config import AssetClassName

LIVE_EOD_ASSET_CLASSES: frozenset[AssetClassName] = frozenset({"etf", "share"})


def _exchange_today(ts: pd.Timestamp) -> str:
    if ts.tzinfo is not None:
        return pd.Timestamp.now(tz=ts.tzinfo).strftime("%Y-%m-%d")
    return pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")


def _trade_date(ts: pd.Timestamp) -> str:
    if ts.tzinfo is not None:
        return ts.strftime("%Y-%m-%d")
    return pd.Timestamp(ts).normalize().strftime("%Y-%m-%d")


def _format_bars(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    for col in ("open", "high", "low", "close", "volume"):
        if col not in out.columns:
            out[col] = pd.NA
    out.index = trade_date_index(pd.DatetimeIndex(out.index))
    return out[["open", "high", "low", "close", "volume"]].astype(float, errors="ignore")


def fetch_provisional_eod_bar(symbol: str) -> pd.DataFrame:
    """Latest yfinance quote for the current session, shaped as one daily bar."""
    ticker = yf.Ticker(resolve_fetch_ticker(symbol))

    daily = ticker.history(period="5d", interval="1d", auto_adjust=True)
    if not daily.empty:
        daily.index = pd.DatetimeIndex(daily.index)
        last_ts = daily.index[-1]
        if _trade_date(last_ts) == _exchange_today(last_ts):
            return _format_bars(daily.tail(1))

    intraday = ticker.history(period="1d", interval="5m", auto_adjust=True)
    if intraday.empty:
        return pd.DataFrame()

    intraday.index = pd.DatetimeIndex(intraday.index)
    last_ts = intraday.index[-1]
    if _trade_date(last_ts) != _exchange_today(last_ts):
        return pd.DataFrame()

    session_date = last_ts.date()
    session = intraday[intraday.index.date == session_date]
    if session.empty:
        return pd.DataFrame()

    close = float(session.iloc[-1]["Close"])
    if close <= 0 or pd.isna(close):
        return pd.DataFrame()

    row = {
        "open": float(session.iloc[0]["Open"]),
        "high": float(session["High"].max()),
        "low": float(session["Low"].min()),
        "close": close,
        "volume": float(session["Volume"].sum()),
    }
    idx = trade_date_index(pd.DatetimeIndex([last_ts]))
    return pd.DataFrame([row], index=idx)


def merge_provisional_eod(bars: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Append or update today's bar when a newer live quote is available."""
    provisional = fetch_provisional_eod_bar(symbol)
    if provisional.empty:
        return bars

    base = normalize_daily_bars(bars.copy()) if not bars.empty else pd.DataFrame()
    provisional = normalize_daily_bars(provisional)
    prov_date = provisional.index[-1]
    prov_row = provisional.iloc[-1]
    close = float(prov_row["close"])
    if close <= 0 or pd.isna(close):
        return bars

    if base.empty:
        return provisional

    if prov_date not in base.index:
        return pd.concat([base, provisional]).sort_index()

    existing_close = float(base.loc[prov_date, "close"])
    if abs(existing_close - close) < 1e-9:
        return bars

    updated = base.copy()
    row = updated.loc[prov_date].copy()
    row["close"] = close
    if pd.notna(prov_row.get("open")):
        row["open"] = float(prov_row["open"])
    if pd.notna(prov_row.get("high")):
        row["high"] = max(float(row.get("high", close)), float(prov_row["high"]))
    if pd.notna(prov_row.get("low")):
        row["low"] = min(float(row.get("low", close)), float(prov_row["low"]))
    vol = prov_row.get("volume")
    if vol is not None and pd.notna(vol) and float(vol) > 0:
        row["volume"] = float(vol)
    updated.loc[prov_date] = row
    return updated


def augment_live_eod_bars(
    bars: pd.DataFrame,
    symbol: str,
    *,
    asset_class: AssetClassName,
) -> pd.DataFrame:
    """For listed ETFs/shares, merge a provisional today close when available."""
    if asset_class not in LIVE_EOD_ASSET_CLASSES:
        return bars
    return merge_provisional_eod(bars, symbol)


def primary_has_official_today(bars: pd.DataFrame, source: str) -> bool:
    """True when Stooq already returned a complete daily bar for the current session."""
    if source != "stooq" or bars.empty:
        return False
    last_ts = bars.index[-1]
    if _trade_date(last_ts) != _exchange_today(last_ts):
        return False
    vol = bars.iloc[-1].get("volume")
    return vol is not None and pd.notna(vol) and float(vol) > 0
