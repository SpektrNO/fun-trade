from funtrade.execution.paper import _position_after_trade
from funtrade.models.perturbation import signal_from_epsilon


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
