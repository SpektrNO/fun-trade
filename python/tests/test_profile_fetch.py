"""Tests for automated fund profile fetchers."""

import pytest

from funtrade.portfolio.eod_profiles import eod_ticker_for, parse_eod_etf_profile
from funtrade.portfolio.nordnet_profiles import nordnet_fund_url, parse_nordnet_fund_html
from funtrade.portfolio.profile_fetch import fetch_profile_for_symbol


def test_eod_ticker_maps_xetra():
    assert eod_ticker_for("VWCE.DE") == "VWCE.XETRA"


def test_parse_eod_etf_profile():
    payload = {
        "General": {"Name": "Vanguard FTSE All-World UCITS ETF"},
        "ETF_Data": {
            "UpdatedAt": "2026-03-31",
            "World_Regions": {
                "North America": {"Equity_%": "63.0"},
                "Europe Developed": {"Equity_%": "17.0"},
                "Asia Developed": {"Equity_%": "10.0"},
                "Asia Emerging": {"Equity_%": "10.0"},
            },
            "Sector_Weights": {
                "Technology": {"Equity_%": "26.0"},
                "Financial Services": {"Equity_%": "16.0"},
                "Healthcare": {"Equity_%": "58.0"},
            },
            "Asset_Allocation": {
                "Stock US": {"Net_Assets_%": "98.0"},
                "Cash": {"Net_Assets_%": "2.0"},
            },
        },
    }
    profile = parse_eod_etf_profile("VWCE.DE", payload)
    assert profile.symbol == "VWCE.DE"
    assert profile.name.startswith("Vanguard")
    assert abs(sum(profile.regions.values()) - 1.0) < 0.02
    assert "Financials" in profile.sectors
    assert profile.asset_classes["Equity"] == pytest.approx(0.98, abs=0.01)


def test_parse_nordnet_fund_html_regions_and_sectors():
    html = (
        '<h2>KLP AksjeFremvoksende Markeder Indeks N</h2>'
        '\\"updatedAt\\":\\"2026-06-30\\",'
        '\\"regions\\":[{\\"displayName\\":\\"Asia\\",\\"weight\\":51.62},'
        '{\\"displayName\\":\\"Latin-Amerika\\",\\"weight\\":6.27}],'
        '\\"sectors\\":[{\\"displayName\\":\\"Teknologi\\",\\"weight\\":45.6},'
        '{\\"displayName\\":\\"Finans\\",\\"weight\\":19.14}],'
        '\\"assets\\":[{\\"displayName\\":\\"Aksjer\\",\\"weight\\":98.98},'
        '{\\"displayName\\":\\"Kontanter\\",\\"weight\\":1.02}]'
    )
    profile = parse_nordnet_fund_html(
        html, symbol="KLP.EM", slug="klp-aksje-fremvoksende-markeder-indeks-nok-8e00e38f",
    )
    assert profile.name == "KLP AksjeFremvoksende Markeder Indeks N"
    assert profile.as_of == "2026-06-30"
    assert profile.regions["Asia"] == pytest.approx(0.891, abs=0.01)
    assert profile.sectors["Technology"] == pytest.approx(0.704, abs=0.01)
    assert profile.asset_classes["Equity"] == pytest.approx(0.989, abs=0.01)


def test_nordnet_fund_url_from_slug():
    assert nordnet_fund_url("klp-foo").endswith("/klp-foo")
    assert "klp-foo" in nordnet_fund_url(
        "https://www.nordnet.no/fond/liste/klp-foo",
    )
    assert nordnet_fund_url("vanguard-ftse-all-world-ucits-vwce-xeta").endswith(
        "/vanguard-ftse-all-world-ucits-vwce-xeta",
    )
    assert "etf/liste/" in nordnet_fund_url("etf/liste/vanguard-ftse-all-world-ucits-vwce-xeta")


def test_fetch_profile_for_symbol_routes_mutual_fund_to_nordnet(monkeypatch):
    from funtrade.config import Settings

    settings = Settings.from_env()
    monkeypatch.setattr(
        "funtrade.portfolio.profile_fetch.load_nordnet_slugs",
        lambda: {"DNB.TEKNOLOGIA": "dnb-teknologi-a-nok-8e00e38f"},
    )
    monkeypatch.setattr(
        "funtrade.portfolio.profile_fetch.fetch_nordnet_fund_profile",
        lambda slug, symbol: parse_nordnet_fund_html(
            '\\"regions\\":[{\\"displayName\\":\\"Asia\\",\\"weight\\":100.0}],'
            '\\"sectors\\":[{\\"displayName\\":\\"Teknologi\\",\\"weight\\":100.0}],'
            '\\"assets\\":[{\\"displayName\\":\\"Aksjer\\",\\"weight\\":100.0}]',
            symbol=symbol,
            slug=slug,
        ),
    )
    profile = fetch_profile_for_symbol("DNB.TEKNOLOGIA", settings=settings, source="nordnet")
    assert profile.symbol == "DNB.TEKNOLOGIA"
    assert "Asia" in profile.regions


def test_fetch_profile_for_symbol_routes_etf_to_nordnet_when_slug_present(monkeypatch):
    from funtrade.config import Settings

    settings = Settings.from_env()
    monkeypatch.setattr(
        "funtrade.portfolio.profile_fetch.load_nordnet_slugs",
        lambda: {"VWCE.DE": "vanguard-ftse-all-world-ucits-etf-eur-abc12345"},
    )
    monkeypatch.setattr(
        "funtrade.portfolio.profile_fetch.fetch_nordnet_fund_profile",
        lambda slug, symbol: parse_nordnet_fund_html(
            '\\"regions\\":[{\\"displayName\\":\\"USA\\",\\"weight\\":100.0}],'
            '\\"sectors\\":[{\\"displayName\\":\\"Teknologi\\",\\"weight\\":100.0}],'
            '\\"assets\\":[{\\"displayName\\":\\"Aksjer\\",\\"weight\\":100.0}]',
            symbol=symbol,
            slug=slug,
        ),
    )
    profile = fetch_profile_for_symbol("VWCE.DE", settings=settings, source="auto")
    assert profile.symbol == "VWCE.DE"
    assert profile.regions["North America"] == pytest.approx(1.0)


def test_fetch_profile_for_symbol_requires_eod_token(monkeypatch):
    from funtrade.config import Settings

    settings = Settings.from_env()
    monkeypatch.delenv("EOD_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="EOD_API_TOKEN"):
        fetch_profile_for_symbol("VWCE.DE", settings=settings, source="eod")
