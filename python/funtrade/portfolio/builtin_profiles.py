"""Built-in fund profiles for symbols without Nordnet/EOD coverage."""

from __future__ import annotations

from funtrade.portfolio.fund_profiles import FundProfile

_BUILTIN: dict[str, FundProfile] = {
    "iShares.Bitcoin.Trust.ETF": FundProfile(
        symbol="iShares.Bitcoin.Trust.ETF",
        name="iShares Bitcoin Trust ETF (IBIT)",
        as_of="2026-07-21",
        source="builtin (spot Bitcoin ETP)",
        regions={"North America": 1.0},
        sectors={"Digital Assets": 1.0},
        asset_classes={"Digital Assets": 1.0},
    ),
    "iShares.Ethereum.Trust.ETF": FundProfile(
        symbol="iShares.Ethereum.Trust.ETF",
        name="iShares Ethereum Trust ETF (ETHA)",
        as_of="2026-07-21",
        source="builtin (spot Ethereum ETP)",
        regions={"North America": 1.0},
        sectors={"Digital Assets": 1.0},
        asset_classes={"Digital Assets": 1.0},
    ),
}


def get_builtin_fund_profile(symbol: str) -> FundProfile | None:
    key = symbol.strip()
    if not key:
        return None
    return _BUILTIN.get(key) or _BUILTIN.get(key.upper())
