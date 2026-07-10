"""Plotly charts with pan/zoom (st.plotly_chart)."""

from __future__ import annotations

import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from funtrade.ui.plotting.base import ChartRenderer
from funtrade.ui.plotting.data import prepare_trade_chart_frames

_LAYOUT = dict(
    height=360,
    margin=dict(l=40, r=20, t=40, b=40),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
)
_AXIS_ZOOMABLE = dict(fixedrange=False)


def _chart_key(*parts: str) -> str:
    raw = "-".join(p for p in parts if p).lower()
    raw = re.sub(r"[^a-z0-9\-]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    return f"plotly-{raw}"[:80]


def _show(fig: go.Figure, *, key: str) -> None:
    fig.update_layout(**_LAYOUT)
    fig.update_xaxes(**_AXIS_ZOOMABLE)
    fig.update_yaxes(**_AXIS_ZOOMABLE)
    st.plotly_chart(fig, use_container_width=True, key=key)


def _time_series_figure(df: pd.DataFrame, *, x: str, y: str | list[str], title: str | None) -> go.Figure:
    plot_df = df.copy()
    plot_df[x] = pd.to_datetime(plot_df[x])
    cols = [y] if isinstance(y, str) else list(y)
    fig = go.Figure()
    for col in cols:
        if col not in plot_df.columns:
            continue
        fig.add_trace(go.Scatter(x=plot_df[x], y=plot_df[col], mode="lines", name=col))
    if title:
        fig.update_layout(title=title)
    return fig


class PlotlyRenderer(ChartRenderer):
    def render_time_series(
        self,
        df: pd.DataFrame,
        *,
        x: str,
        y: str | list[str],
        title: str | None = None,
    ) -> None:
        if title:
            st.subheader(title)
        y_key = y if isinstance(y, str) else "-".join(y)
        _show(_time_series_figure(df, x=x, y=y, title=None), key=_chart_key("ts", title or y_key))

    def render_trade_charts(
        self,
        series: pd.DataFrame,
        *,
        epsilon_threshold: float,
        currency: str,
        trend_enable: bool = False,
        trend_gate_z: float | None = None,
    ) -> None:
        charts = prepare_trade_chart_frames(
            series,
            epsilon_threshold=epsilon_threshold,
            trend_enable=trend_enable,
            trend_gate_z=trend_gate_z,
        )

        st.subheader("ε")
        st.caption(f"Buy/sell band at ±{epsilon_threshold:.2f}")
        _show(
            _time_series_figure(
                charts["epsilon"], x="time", y=["epsilon", "upper", "lower", "zero"], title=None,
            ),
            key=_chart_key("trade", "epsilon"),
        )

        st.subheader(f"Price ({currency})")
        _show(
            _time_series_figure(charts["price"], x="time", y="price", title=None),
            key=_chart_key("trade", "price"),
        )

        if "z_trend" in charts:
            st.subheader("Trend (z_trend)")
            z_cols = ["z_trend"]
            if "gate" in charts["z_trend"].columns:
                z_cols.extend(["gate", "neg_gate"])
            _show(
                _time_series_figure(charts["z_trend"], x="time", y=z_cols, title=None),
                key=_chart_key("trade", "z-trend"),
            )

    def render_pnl_with_trades(self, df: pd.DataFrame) -> None:
        plot_df = df.copy()
        if "time" in plot_df.columns:
            plot_df["time"] = pd.to_datetime(plot_df["time"])
            x = plot_df["time"]
        else:
            x = pd.to_datetime(plot_df.index)

        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(
            go.Scatter(x=x, y=plot_df["realized_pnl"], name="Realized PnL (EUR)", line=dict(color="#2ecc71")),
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(x=x, y=plot_df["unrealized_pnl"], name="Unrealized PnL (EUR)", line=dict(color="#3498db")),
            secondary_y=False,
        )

        bought = plot_df["shares_bought"].fillna(0.0)
        sold = plot_df["shares_sold"].fillna(0.0)
        buy_mask = bought > 0
        sell_mask = sold > 0
        if buy_mask.any():
            fig.add_trace(
                go.Bar(x=x[buy_mask], y=bought[buy_mask], name="Shares bought", marker_color="#27ae60", opacity=0.45),
                secondary_y=True,
            )
        if sell_mask.any():
            fig.add_trace(
                go.Bar(x=x[sell_mask], y=-sold[sell_mask], name="Shares sold", marker_color="#e74c3c", opacity=0.45),
                secondary_y=True,
            )

        fig.update_yaxes(title_text="PnL (EUR)", secondary_y=False)
        fig.update_yaxes(title_text="Shares traded", secondary_y=True)
        fig.update_layout(title="Realized vs unrealized PnL")
        _show(fig, key=_chart_key("pnl", "trades"))
