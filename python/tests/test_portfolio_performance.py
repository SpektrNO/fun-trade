import pandas as pd
import pytest

from funtrade.portfolio.performance import (
    change_arrow,
    format_change_cell,
    holding_pnl,
    pct_change,
    price_on_or_before,
)


def test_pct_change():
    assert pct_change(100.0, 110.0) == pytest.approx(10.0)
    assert pct_change(100.0, 90.0) == pytest.approx(-10.0)


def test_change_arrow_flat_within_tolerance():
    assert change_arrow(0.01) == "→"
    assert change_arrow(1.0) == "▲"
    assert change_arrow(-1.0) == "▼"
    assert change_arrow(None) == "—"


def test_format_change_cell():
    assert format_change_cell(1.234, "▲") == "▲ +1.23%"
    assert format_change_cell(-2.5, "▼") == "▼ -2.50%"
    assert format_change_cell(None, "—") == "—"


def test_holding_pnl_from_shares():
    assert holding_pnl(shares=10.0, value_latest=None, base_price=100.0, latest_price=110.0) == pytest.approx(100.0)


def test_holding_pnl_prefers_value_amount_over_shares():
    # Value path wins so PnL stays in portfolio currency even if shares×price is another unit.
    pnl = holding_pnl(shares=10.0, value_latest=1100.0, base_price=100.0, latest_price=110.0)
    assert pnl == pytest.approx(100.0)


def test_holding_pnl_from_value_amount():
    # Current value 1100 at latest 110 ⇒ 10 shares; base 100 ⇒ PnL 100
    pnl = holding_pnl(shares=None, value_latest=1100.0, base_price=100.0, latest_price=110.0)
    assert pnl == pytest.approx(100.0)


def test_price_on_or_before_uses_last_available():
    idx = pd.DatetimeIndex(["2026-07-15", "2026-07-17", "2026-07-20"], tz="UTC")
    bars = pd.DataFrame({"price": [100.0, 105.0, 110.0]}, index=idx)
    price, as_of = price_on_or_before(bars, pd.Timestamp("2026-07-19").date())
    assert price == pytest.approx(105.0)
    assert as_of.isoformat() == "2026-07-17"
