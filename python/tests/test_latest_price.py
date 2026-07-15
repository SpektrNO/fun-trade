import pandas as pd

from funtrade.data import latest_price as lp


def _bar(date: str, close: float) -> pd.DataFrame:
    idx = pd.DatetimeIndex([date], tz="UTC")
    return pd.DataFrame(
        {"open": [close], "high": [close], "low": [close], "close": [close], "volume": [1000.0]},
        index=idx,
    )


def test_augment_live_eod_skips_mutual_funds():
    bars = _bar("2026-07-13", 100.0)
    out = lp.augment_live_eod_bars(bars, "NO0010336977", asset_class="mutual_fund")
    assert out.equals(bars)


def test_merge_provisional_eod_appends_today(monkeypatch):
    bars = _bar("2026-07-13", 100.0)
    today = _bar("2026-07-14", 101.5)

    monkeypatch.setattr(lp, "fetch_provisional_eod_bar", lambda _symbol: today)

    out = lp.merge_provisional_eod(bars, "VWCE.DE")
    assert len(out) == 2
    assert float(out.iloc[-1]["close"]) == 101.5


def test_merge_provisional_eod_updates_changed_close(monkeypatch):
    bars = _bar("2026-07-14", 100.0)
    today = _bar("2026-07-14", 101.25)

    monkeypatch.setattr(lp, "fetch_provisional_eod_bar", lambda _symbol: today)

    out = lp.merge_provisional_eod(bars, "VWCE.DE")
    assert len(out) == 1
    assert float(out.iloc[-1]["close"]) == 101.25


def test_merge_provisional_eod_noop_when_quote_unchanged(monkeypatch):
    bars = _bar("2026-07-14", 100.0)
    monkeypatch.setattr(lp, "fetch_provisional_eod_bar", lambda _symbol: _bar("2026-07-14", 100.0))

    out = lp.merge_provisional_eod(bars, "VWCE.DE")
    assert out is bars


def test_primary_has_official_today_when_stooq_returned_session_bar(monkeypatch):
    bars = pd.DataFrame(
        {"open": [100.0], "high": [101.0], "low": [99.5], "close": [100.0], "volume": [50000.0]},
        index=pd.DatetimeIndex(["2026-07-14"], tz="UTC"),
    )
    monkeypatch.setattr(lp, "_exchange_today", lambda _ts: "2026-07-14")
    monkeypatch.setattr(lp, "_trade_date", lambda ts: ts.strftime("%Y-%m-%d"))
    assert lp.primary_has_official_today(bars, "stooq") is True


def test_primary_has_official_today_false_for_yfinance_source():
    bars = _bar("2026-07-14", 100.0)
    assert lp.primary_has_official_today(bars, "yfinance") is False


def test_fetch_provisional_eod_uses_daily_when_it_includes_today(monkeypatch):
    idx = pd.DatetimeIndex(["2026-07-13", "2026-07-14"], tz="Europe/Berlin")
    daily = pd.DataFrame(
        {
            "Open": [99.0, 100.0],
            "High": [100.0, 101.0],
            "Low": [98.5, 99.5],
            "Close": [99.5, 100.5],
            "Volume": [1000, 2000],
        },
        index=idx,
    )

    class _Ticker:
        def history(self, **kwargs):
            if kwargs.get("interval") == "1d":
                return daily
            return pd.DataFrame()

    monkeypatch.setattr(lp.yf, "Ticker", lambda _t: _Ticker())
    monkeypatch.setattr(lp, "_exchange_today", lambda _ts: "2026-07-14")
    monkeypatch.setattr(lp, "_trade_date", lambda ts: ts.strftime("%Y-%m-%d"))

    out = lp.fetch_provisional_eod_bar("VWCE.DE")
    assert len(out) == 1
    assert float(out.iloc[-1]["close"]) == 100.5


def test_fetch_provisional_eod_falls_back_to_intraday(monkeypatch):
    idx = pd.DatetimeIndex(["2026-07-13"], tz="Europe/Berlin")
    daily = pd.DataFrame(
        {
            "Open": [99.0],
            "High": [100.0],
            "Low": [98.5],
            "Close": [99.5],
            "Volume": [1000],
        },
        index=idx,
    )
    intraday_idx = pd.DatetimeIndex(
        ["2026-07-14 10:00:00", "2026-07-14 10:05:00"],
        tz="Europe/Berlin",
    )
    intraday = pd.DataFrame(
        {
            "Open": [100.0, 100.2],
            "High": [100.3, 100.8],
            "Low": [99.8, 100.1],
            "Close": [100.2, 100.6],
            "Volume": [500, 700],
        },
        index=intraday_idx,
    )

    class _Ticker:
        def history(self, **kwargs):
            if kwargs.get("interval") == "1d":
                return daily
            if kwargs.get("interval") == "5m":
                return intraday
            return pd.DataFrame()

    monkeypatch.setattr(lp.yf, "Ticker", lambda _t: _Ticker())
    monkeypatch.setattr(lp, "_exchange_today", lambda _ts: "2026-07-14")
    monkeypatch.setattr(lp, "_trade_date", lambda ts: ts.strftime("%Y-%m-%d"))

    out = lp.fetch_provisional_eod_bar("VWCE.DE")
    assert len(out) == 1
    assert float(out.iloc[-1]["close"]) == 100.6
    assert float(out.iloc[-1]["open"]) == 100.0
    assert float(out.iloc[-1]["volume"]) == 1200.0
