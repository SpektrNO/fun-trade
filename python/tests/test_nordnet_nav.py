import pandas as pd

from funtrade.data import nordnet_nav as nn


def _bar(date: str, close: float) -> pd.DataFrame:
    idx = pd.DatetimeIndex([date], tz="UTC")
    return pd.DataFrame(
        {"open": [close], "high": [close], "low": [close], "close": [close], "volume": [0.0]},
        index=idx,
    )


def test_merge_nordnet_nav_bar_appends_newer_nav(monkeypatch):
    bars = _bar("2026-07-16", 202.9796)

    monkeypatch.setattr(nn, "fetch_latest_nordnet_nav", lambda _symbol: ("2026-07-17", 193.3697))

    out = nn.merge_nordnet_nav_bar(bars, "DNB.Fund.Asian.Mid.Cap.N.NOK.Acc")
    assert len(out) == 2
    assert out.index[-1].strftime("%Y-%m-%d") == "2026-07-17"
    assert float(out.iloc[-1]["close"]) == 193.3697


def test_merge_nordnet_nav_bar_updates_same_day_correction(monkeypatch):
    bars = _bar("2026-07-17", 200.0)

    monkeypatch.setattr(nn, "fetch_latest_nordnet_nav", lambda _symbol: ("2026-07-17", 193.3697))

    out = nn.merge_nordnet_nav_bar(bars, "DNB.Fund.Asian.Mid.Cap.N.NOK.Acc")
    assert len(out) == 1
    assert float(out.iloc[-1]["close"]) == 193.3697


def test_merge_nordnet_nav_bar_noop_when_ingest_is_current(monkeypatch):
    bars = _bar("2026-07-17", 193.3697)

    monkeypatch.setattr(nn, "fetch_latest_nordnet_nav", lambda _symbol: ("2026-07-17", 193.3697))

    out = nn.merge_nordnet_nav_bar(bars, "DNB.Fund.Asian.Mid.Cap.N.NOK.Acc")
    assert out is bars


def test_merge_nordnet_nav_bar_noop_when_nordnet_missing(monkeypatch):
    bars = _bar("2026-07-16", 202.9796)
    monkeypatch.setattr(nn, "fetch_latest_nordnet_nav", lambda _symbol: None)

    out = nn.merge_nordnet_nav_bar(bars, "DNB.Fund.Asian.Mid.Cap.N.NOK.Acc")
    assert out is bars


def test_parse_latest_nav_from_initial_props():
    html = (
        'window.__initialProps__="{\\"initialProps\\":{\\"sharedInitialProps\\":'
        '{\\"initialFundData\\":{\\"navInfo\\":{\\"latestNav\\":'
        '{\\"date\\":\\"2026-07-17\\",\\"value\\":193.3697}}}}}}";'
    )
    parsed = nn._parse_latest_nav(html)
    assert parsed == ("2026-07-17", 193.3697)
