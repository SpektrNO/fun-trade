"""FX conversion helpers (yfinance spot rates)."""

from __future__ import annotations

from functools import lru_cache

import yfinance as yf


@lru_cache(maxsize=32)
def fx_rate(from_ccy: str, to_ccy: str) -> float:
    """Latest FX rate: `amount_in_to = amount_in_from * rate`. Supports EUR/USD/NOK."""
    from_ccy = from_ccy.upper().strip()
    to_ccy = to_ccy.upper().strip()
    if from_ccy == to_ccy:
        return 1.0

    if from_ccy == "EUR" and to_ccy == "USD":
        ticker = "EURUSD=X"
        return float(yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=True).iloc[-1]["Close"])
    if from_ccy == "USD" and to_ccy == "EUR":
        ticker = "EURUSD=X"
        eurusd = float(yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=True).iloc[-1]["Close"])
        return 1.0 / eurusd
    if from_ccy == "EUR" and to_ccy == "NOK":
        ticker = "EURNOK=X"
        return float(yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=True).iloc[-1]["Close"])
    if from_ccy == "NOK" and to_ccy == "EUR":
        ticker = "EURNOK=X"
        eurnok = float(yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=True).iloc[-1]["Close"])
        return 1.0 / eurnok
    if from_ccy == "USD" and to_ccy == "NOK":
        ticker = "USDNOK=X"
        return float(yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=True).iloc[-1]["Close"])
    if from_ccy == "NOK" and to_ccy == "USD":
        ticker = "USDNOK=X"
        usdnok = float(yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=True).iloc[-1]["Close"])
        return 1.0 / usdnok

    raise ValueError(f"Unsupported FX conversion {from_ccy!r} -> {to_ccy!r}")


def convert_currency_value(value: float, *, from_ccy: str, to_ccy: str) -> float:
    return float(value) * fx_rate(from_ccy, to_ccy)
