import pytest

from funtrade.portfolio.values import (
    holding_value_in_currency,
    portfolio_holding_values,
    portfolio_weight_fractions,
    portfolio_weight_pcts,
)
from funtrade.portfolio_config import PortfolioConfig, PortfolioHolding


def _identity_convert(value: float, *, from_ccy: str, to_ccy: str) -> float:
    assert from_ccy == "NOK"
    assert to_ccy == "EUR"
    return value * 2.0


def test_portfolio_weights_from_mixed_currency_values():
    portfolio = PortfolioConfig(
        name="Spektr",
        currency="NOK",
        valuation_mode="weight_pct",
        holdings=(
            PortfolioHolding(symbol="A", value_nok=800_000.0),
            PortfolioHolding(symbol="B", value_usd=10_000.0),
        ),
    )

    def convert(value: float, *, from_ccy: str, to_ccy: str) -> float:
        if from_ccy == "USD" and to_ccy == "NOK":
            return value * 10.0
        return value

    values = portfolio_holding_values(portfolio, convert=convert)
    assert values["A"] == pytest.approx(800_000.0)
    assert values["B"] == pytest.approx(100_000.0)

    weights = portfolio_weight_pcts(portfolio, convert=convert)
    assert weights["A"] == pytest.approx(88.89, abs=0.01)
    assert weights["B"] == pytest.approx(11.11, abs=0.01)
    assert sum(weights.values()) == pytest.approx(100.0, abs=0.05)


def test_portfolio_weights_fall_back_to_weight_pct_without_values():
    portfolio = PortfolioConfig(
        name="Test",
        currency="EUR",
        valuation_mode="weight_pct",
        holdings=(
            PortfolioHolding(symbol="VWCE.DE", weight_pct=60.0),
            PortfolioHolding(symbol="AGGH.DE", weight_pct=40.0),
        ),
    )
    weights = portfolio_weight_pcts(portfolio)
    assert weights == {"VWCE.DE": 60.0, "AGGH.DE": 40.0}


def test_holding_value_legacy_value_eur_used_as_nok():
    holding = PortfolioHolding(symbol="VWCE.DE", value_eur=123.0)
    assert holding_value_in_currency(holding, "NOK") == pytest.approx(123.0)


def test_holding_value_converts_nok_to_eur():
    holding = PortfolioHolding(symbol="VWCE.DE", value_nok=10.0)
    assert holding_value_in_currency(holding, "EUR", convert=_identity_convert) == pytest.approx(20.0)


def test_portfolio_weight_fractions_normalize_weight_pct():
    portfolio = PortfolioConfig(
        name="Test",
        currency="EUR",
        valuation_mode="weight_pct",
        holdings=(
            PortfolioHolding(symbol="A", weight_pct=30.0),
            PortfolioHolding(symbol="B", weight_pct=70.0),
        ),
    )
    fractions = portfolio_weight_fractions(portfolio)
    assert fractions["A"] == pytest.approx(0.3)
    assert fractions["B"] == pytest.approx(0.7)
