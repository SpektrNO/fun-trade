from funtrade.data.ingest import _fetch_bars
from funtrade.data.stooq import StooqPriceProvider
from funtrade.data.symbols import resolve_fetch_ticker, symbol_aliases
from funtrade.data.yfinance_provider import YFinancePriceProvider


def test_resolve_fetch_ticker_isin():
    assert resolve_fetch_ticker("NO0010336977") == "0P00000O4C.IR"
    assert resolve_fetch_ticker("no0010336977") == "0P00000O4C.IR"


def test_resolve_fetch_ticker_friendly_name():
    assert resolve_fetch_ticker("DNB-BARNE.IR") == "0P00000O4C.IR"


def test_resolve_fetch_ticker_futures():
    assert resolve_fetch_ticker("BZ=F") == "BZ=F"
    assert resolve_fetch_ticker("CL=F") == "CL=F"


def test_resolve_fetch_ticker_xetra_default():
    assert resolve_fetch_ticker("VWCE.DE") == "VWCE.DE"
    assert resolve_fetch_ticker("VWCE") == "VWCE.DE"


def test_resolve_fetch_ticker_env_override(monkeypatch):
    monkeypatch.setenv("SYMBOL_ALIASES", "MYFUND.XX=0P00001234.IR")
    assert symbol_aliases()["MYFUND.XX"] == "0P00001234.IR"
    assert resolve_fetch_ticker("MYFUND.XX") == "0P00001234.IR"


def test_fetch_bars_uses_yfinance_for_aliased_symbol():
    import pandas as pd

    end = pd.Timestamp.now(tz="UTC").normalize()
    start = end - pd.Timedelta(days=30)
    bars, source = _fetch_bars("NO0010336977", start, end, StooqPriceProvider())
    assert source == "yfinance"
    assert not bars.empty
