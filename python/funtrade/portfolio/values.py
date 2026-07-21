"""Portfolio holding amounts and weight estimation from position values."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from funtrade.data.fx import convert_currency_value
from funtrade.portfolio_config import PortfolioConfig, PortfolioHolding


class CurrencyConverter(Protocol):
    def __call__(self, value: float, *, from_ccy: str, to_ccy: str) -> float: ...


def _portfolio_currency(portfolio: PortfolioConfig) -> str:
    return str(portfolio.currency or "EUR").upper().strip()


def holding_has_value_amount(holding: PortfolioHolding) -> bool:
    return any(v is not None for v in (holding.value_eur, holding.value_nok, holding.value_usd))


def portfolio_has_value_amounts(portfolio: PortfolioConfig) -> bool:
    return any(holding_has_value_amount(h) for h in portfolio.holdings)


def holding_value_in_currency(
    holding: PortfolioHolding,
    currency: str,
    *,
    convert: CurrencyConverter = convert_currency_value,
) -> float | None:
    """Return the holding amount in `currency`, converting when needed."""
    currency = currency.upper().strip()
    if currency == "NOK":
        if holding.value_nok is not None:
            return float(holding.value_nok)
        if holding.value_eur is not None:
            # Legacy: some portfolio files store NOK amounts in `value_eur`.
            return float(holding.value_eur)
        if holding.value_usd is not None:
            return convert(float(holding.value_usd), from_ccy="USD", to_ccy="NOK")
    elif currency == "EUR":
        if holding.value_eur is not None:
            return float(holding.value_eur)
        if holding.value_usd is not None:
            return convert(float(holding.value_usd), from_ccy="USD", to_ccy="EUR")
        if holding.value_nok is not None:
            return convert(float(holding.value_nok), from_ccy="NOK", to_ccy="EUR")
    elif currency == "USD":
        if holding.value_usd is not None:
            return float(holding.value_usd)
        if holding.value_eur is not None:
            return convert(float(holding.value_eur), from_ccy="EUR", to_ccy="USD")
        if holding.value_nok is not None:
            return convert(float(holding.value_nok), from_ccy="NOK", to_ccy="USD")
    else:
        if holding.value_eur is not None:
            return float(holding.value_eur)
        if holding.value_nok is not None:
            return float(holding.value_nok)
        if holding.value_usd is not None:
            return float(holding.value_usd)
    return None


def portfolio_holding_values(
    portfolio: PortfolioConfig,
    *,
    convert: CurrencyConverter = convert_currency_value,
) -> dict[str, float]:
    """Symbol → position value in the portfolio currency."""
    currency = _portfolio_currency(portfolio)
    values: dict[str, float] = {}
    for h in portfolio.holdings:
        amount = holding_value_in_currency(h, currency, convert=convert)
        if amount is not None and amount > 0:
            values[h.symbol] = amount
    return values


def portfolio_weight_fractions(
    portfolio: PortfolioConfig,
    *,
    convert: CurrencyConverter = convert_currency_value,
) -> dict[str, float]:
    """Symbol → portfolio weight as a 0–1 fraction."""
    if portfolio_has_value_amounts(portfolio):
        values = portfolio_holding_values(portfolio, convert=convert)
        total = sum(values.values())
        if total <= 0:
            return {}
        return {sym: val / total for sym, val in values.items()}

    if portfolio.valuation_mode == "weight_pct":
        raw = {h.symbol: float(h.weight_pct or 0.0) for h in portfolio.holdings}
        total = sum(raw.values())
        if total <= 0:
            return {}
        return {sym: w / total for sym, w in raw.items()}

    if portfolio.valuation_mode == "value_eur":
        raw = {h.symbol: float(h.value_eur or 0.0) for h in portfolio.holdings}
        total = sum(raw.values())
        if total <= 0:
            return {}
        return {sym: w / total for sym, w in raw.items()}

    return {}


def portfolio_weight_pcts(
    portfolio: PortfolioConfig,
    *,
    convert: CurrencyConverter = convert_currency_value,
) -> dict[str, float]:
    """Symbol → portfolio weight percent (0–100)."""
    return {sym: round(frac * 100.0, 2) for sym, frac in portfolio_weight_fractions(portfolio, convert=convert).items()}
