"""Abstract chart renderer for the Streamlit console."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class ChartRenderer(ABC):
    """Library-agnostic chart API; concrete backends render via Streamlit, Plotly, etc."""

    @abstractmethod
    def render_time_series(
        self,
        df: pd.DataFrame,
        *,
        x: str,
        y: str | list[str],
        title: str | None = None,
        chart_key: str | None = None,
    ) -> None:
        """Single time-series line chart (optionally multiple y columns)."""

    @abstractmethod
    def render_epsilon_chart(
        self,
        df: pd.DataFrame,
        *,
        epsilon_threshold: float,
        chart_key: str | None = None,
    ) -> None:
        """ε with ±threshold bands and regime_valid shading when present."""

    @abstractmethod
    def render_trade_charts(
        self,
        series: pd.DataFrame,
        *,
        epsilon_threshold: float,
        currency: str,
        trend_enable: bool = False,
        trend_gate_z: float | None = None,
        momentum_overlay: pd.DataFrame | None = None,
        rsi_chart: pd.DataFrame | None = None,
        rsi_params: dict | None = None,
    ) -> None:
        """Trade tab: ε bands, price (+ optional MA/Bollinger + RSI panel), and z_trend."""

    @abstractmethod
    def render_allocation_bars(
        self,
        df: pd.DataFrame,
        *,
        title: str | None = None,
        chart_key: str | None = None,
    ) -> None:
        """Horizontal bar chart of category weights (weight_pct column)."""

    @abstractmethod
    def render_pnl_with_trades(self, df: pd.DataFrame, *, chart_key: str | None = None) -> None:
        """Backtest: realized/unrealized PnL with buy/sell share bars."""
