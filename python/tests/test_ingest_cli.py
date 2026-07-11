from funtrade.cli import _resolve_ingest_symbols


def test_resolve_ingest_symbols_list():
    assert _resolve_ingest_symbols(symbol=None, symbols=["VWCE.DE", "EXSA.DE"]) == [
        "VWCE.DE",
        "EXSA.DE",
    ]


def test_resolve_ingest_symbols_single():
    assert _resolve_ingest_symbols(symbol="VWCE.DE", symbols=None) == ["VWCE.DE"]


def test_resolve_ingest_symbols_list_overrides_single():
    assert _resolve_ingest_symbols(symbol="VWCE.DE", symbols=["EXSA.DE"]) == ["EXSA.DE"]


def test_resolve_ingest_symbols_watchlist_default():
    assert _resolve_ingest_symbols(symbol=None, symbols=None) is None
