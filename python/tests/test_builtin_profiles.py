from funtrade.portfolio.builtin_profiles import get_builtin_fund_profile
from funtrade.portfolio.fund_profiles import load_fund_profile


def test_builtin_crypto_profiles_load():
    btc = load_fund_profile("iShares.Bitcoin.Trust.ETF")
    eth = load_fund_profile("iShares.Ethereum.Trust.ETF")
    assert btc is not None
    assert eth is not None
    assert btc.asset_classes["Digital Assets"] == 1.0
    assert eth.sectors["Digital Assets"] == 1.0


def test_get_builtin_fund_profile_unknown_returns_none():
    assert get_builtin_fund_profile("UNKNOWN.SYMBOL") is None
