import json

import pytest

from funtrade.portfolio.allocation import compute_portfolio_allocation
from funtrade.portfolio.fund_profiles import load_fund_profile
from funtrade.portfolio_config import PortfolioConfig, PortfolioHolding, reset_portfolio_config_cache


def test_load_fund_profile_vwce():
    prof = load_fund_profile("VWCE.DE")
    assert prof is not None
    assert prof.symbol == "VWCE.DE"
    assert abs(sum(prof.regions.values()) - 1.0) < 0.02


def test_look_through_allocation():
    portfolio = PortfolioConfig(
        name="Test",
        currency="EUR",
        valuation_mode="weight_pct",
        holdings=(
            PortfolioHolding(symbol="VWCE.DE", weight_pct=60.0),
            PortfolioHolding(symbol="AGGH.DE", weight_pct=40.0),
        ),
    )
    result = compute_portfolio_allocation(portfolio)
    assert result is not None
    assert result.missing_profiles == ()
    assert not result.regions.empty
    assert not result.asset_classes.empty
    na_row = result.regions.loc[result.regions["category"] == "North America"]
    assert not na_row.empty
    # 60% VWCE * ~63% NA + 40% AGGH * ~42% NA ≈ 54.6%
    assert float(na_row["weight_pct"].iloc[0]) == pytest.approx(54.6, abs=2.0)


def test_look_through_allocation_from_position_values():
    portfolio = PortfolioConfig(
        name="Test",
        currency="NOK",
        valuation_mode="weight_pct",
        holdings=(
            PortfolioHolding(symbol="VWCE.DE", value_nok=750_000.0),
            PortfolioHolding(symbol="AGGH.DE", value_nok=250_000.0),
        ),
    )
    result = compute_portfolio_allocation(portfolio)
    assert result is not None
    assert result.total_weight_pct == pytest.approx(100.0)
    vwce = result.holdings.loc[result.holdings["symbol"] == "VWCE.DE", "portfolio_weight_pct"].iloc[0]
    assert float(vwce) == pytest.approx(75.0)


def test_missing_profile_reported():
    portfolio = PortfolioConfig(
        name="Test",
        currency="EUR",
        valuation_mode="weight_pct",
        holdings=(PortfolioHolding(symbol="UNKNOWN.FUND", weight_pct=100.0),),
    )
    result = compute_portfolio_allocation(portfolio)
    assert result is not None
    assert result.missing_profiles == ("UNKNOWN.FUND",)
    assert result.uncovered_weight_pct == pytest.approx(100.0)
    assert result.regions.empty
