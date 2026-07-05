"""Pluggable chart rendering for the Streamlit console."""

from funtrade.ui.plotting.base import ChartRenderer
from funtrade.ui.plotting.data import prepare_trade_chart_frames
from funtrade.ui.plotting.factory import get_chart_renderer

__all__ = [
    "ChartRenderer",
    "get_chart_renderer",
    "prepare_trade_chart_frames",
]
