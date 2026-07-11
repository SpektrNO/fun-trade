from funtrade.execution.paper import (
    MIN_TRADE_EUR,
    PaperSettings,
    _deviation_scale,
    _position_after_trade,
    compute_trade_qty,
)
from funtrade.models.perturbation import signal_from_epsilon


def _paper(**kwargs) -> PaperSettings:
    defaults = dict(
        initial_cash=100_000.0,
        position_limit_shares=1000.0,
        fee_bps=5.0,
        trade_slice_pct=0.10,
        csv_path=__import__("pathlib").Path("data/paper_trades.csv"),
    )
    defaults.update(kwargs)
    return PaperSettings(**defaults)


def test_paper_skips_invalid_regime():
    assert signal_from_epsilon(3.0, 2.0, False) == 0


def test_paper_signal_buy():
    assert signal_from_epsilon(-2.5, 2.0, True) == 1


def test_position_after_trade_open_long():
    qty, avg, realized = _position_after_trade(0.0, 0.0, "buy", 10.0, 50.0)
    assert qty == 10.0
    assert avg == 50.0
    assert realized == 0.0


def test_position_after_trade_close_long():
    qty, avg, realized = _position_after_trade(10.0, 50.0, "sell", 10.0, 60.0)
    assert qty == 0.0
    assert realized == 100.0


def test_position_after_trade_partial_close():
    qty, avg, realized = _position_after_trade(20.0, 50.0, "sell", 10.0, 55.0)
    assert qty == 10.0
    assert avg == 50.0
    assert realized == 50.0


def test_position_after_trade_fractional():
    qty, avg, realized = _position_after_trade(0.0, 0.0, "buy", 12.345, 87.5)
    assert qty == 12.345
    assert avg == 87.5
    assert realized == 0.0


def test_deviation_scale_extreme_epsilon():
    assert _deviation_scale(-4.0, 2.0) == 0.5
    assert _deviation_scale(4.0, 2.0) == 0.5
    assert _deviation_scale(-2.5, 2.0) == 0.8


def test_compute_trade_qty_buy_uses_ten_pct_slice():
    paper = _paper(initial_cash=100_000.0, trade_slice_pct=0.10)
    qty = compute_trade_qty(side="buy", price=100.0, cash_eur=100_000.0, net_qty=0.0, paper=paper)
    # 10% of 100k = 10k EUR → 100 shares at €100
    assert abs(qty - 100.0) < 0.01


def test_compute_trade_qty_buy_fractional():
    paper = _paper(trade_slice_pct=0.10)
    qty = compute_trade_qty(side="buy", price=137.42, cash_eur=50_000.0, net_qty=0.0, paper=paper)
    notional = qty * 137.42
    assert notional <= 10_000.0 + 1.0
    assert qty != int(qty) or qty < 100  # fractional or sub-100-lot


def test_compute_trade_qty_no_buy_when_broke():
    paper = _paper()
    assert (
        compute_trade_qty(side="buy", price=100.0, cash_eur=0.0, net_qty=0.0, paper=paper) == 0.0
    )
    assert (
        compute_trade_qty(side="buy", price=100.0, cash_eur=0.5, net_qty=0.0, paper=paper) == 0.0
    )


def test_compute_trade_qty_buy_capped_by_cash():
    paper = _paper(trade_slice_pct=0.10)
    qty = compute_trade_qty(side="buy", price=100.0, cash_eur=5_000.0, net_qty=0.0, paper=paper)
    fee_mult = 1.0 + paper.fee_bps / 10000.0
    assert qty * 100.0 * fee_mult <= 5_000.0 + 1e-6


def test_compute_trade_qty_buy_scales_down_when_extreme():
    paper = _paper(trade_slice_pct=0.10)
    normal = compute_trade_qty(
        side="buy", price=100.0, cash_eur=100_000.0, net_qty=0.0, paper=paper,
        epsilon=-2.5, epsilon_threshold=2.0,
    )
    extreme = compute_trade_qty(
        side="buy", price=100.0, cash_eur=100_000.0, net_qty=0.0, paper=paper,
        epsilon=-4.0, epsilon_threshold=2.0,
    )
    assert extreme < normal


def test_compute_trade_qty_sell_incremental():
    paper = _paper(trade_slice_pct=0.10)
    qty = compute_trade_qty(
        side="sell", price=100.0, cash_eur=50_000.0, net_qty=500.0, paper=paper,
    )
    assert abs(qty - 100.0) < 0.01  # full €10k slice at €100/share
    assert qty < 500.0


def test_compute_trade_qty_sell_scales_when_still_extended():
    paper = _paper(trade_slice_pct=0.10)
    mild = compute_trade_qty(
        side="sell", price=100.0, cash_eur=0.0, net_qty=500.0, paper=paper,
        epsilon=2.5, epsilon_threshold=2.0,
    )
    extreme = compute_trade_qty(
        side="sell", price=100.0, cash_eur=0.0, net_qty=500.0, paper=paper,
        epsilon=4.0, epsilon_threshold=2.0,
    )
    assert extreme < mild


def test_min_trade_eur_blocks_dust():
    paper = _paper(trade_slice_pct=0.000001)
    qty = compute_trade_qty(side="buy", price=100.0, cash_eur=100.0, net_qty=0.0, paper=paper)
    assert qty == 0.0
    assert MIN_TRADE_EUR == 1.0
