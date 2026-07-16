"""Plotly charts with pan/zoom (st.plotly_chart)."""

from __future__ import annotations

import hashlib
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from funtrade.ui.plotting.base import ChartRenderer
from funtrade.ui.plotting.data import (
    normalize_chart_times,
    prepare_trade_chart_frames,
    price_chart_series,
    price_rsi_caption,
    regime_invalid_spans,
    rsi_chart_series,
    rsi_threshold_levels,
)

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
    key = f"plotly-{raw}"
    if len(key) > 80:
        digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
        key = f"plotly-{digest}"
    return key


def _show(fig: go.Figure, *, key: str) -> None:
    fig.update_layout(**_LAYOUT)
    fig.update_xaxes(**_AXIS_ZOOMABLE)
    fig.update_yaxes(**_AXIS_ZOOMABLE)
    st.plotly_chart(fig, width="stretch", key=key)


def _time_series_figure(df: pd.DataFrame, *, x: str, y: str | list[str], title: str | None) -> go.Figure:
    plot_df = df.copy()
    plot_df[x] = pd.to_datetime(plot_df[x])
    cols = [y] if isinstance(y, str) else list(y)
    line_styles: dict[str, dict] = {
        "price": dict(line=dict(color="#2c3e50", width=2)),
        "Fair price (H₀)": dict(line=dict(color="#e67e22", dash="dash", width=1.5)),
        "Fair + perturbation (ε)": dict(line=dict(color="#3498db", dash="dot", width=1.5)),
        "Fast MA": dict(line=dict(color="#27ae60", width=1.5)),
        "Slow MA": dict(line=dict(color="#c0392b", width=1.5)),
        "Upper band (+2σ)": dict(line=dict(color="#95a5a6", dash="dash", width=1)),
        "Lower band (−2σ)": dict(line=dict(color="#95a5a6", dash="dash", width=1)),
    }
    fig = go.Figure()
    for col in cols:
        if col not in plot_df.columns:
            continue
        style = line_styles.get(col, {})
        fig.add_trace(go.Scatter(x=plot_df[x], y=plot_df[col], mode="lines", name=col, **style))
    if title:
        fig.update_layout(title=title)
    return fig


def _epsilon_figure(df: pd.DataFrame, *, epsilon_threshold: float) -> go.Figure:
    plot_df = df.copy()
    if plot_df.empty or "epsilon" not in plot_df.columns:
        return go.Figure()
    plot_df["time"] = normalize_chart_times(plot_df["time"])
    x = plot_df["time"]

    fig = go.Figure()
    for start, end in regime_invalid_spans(plot_df):
        fig.add_vrect(
            x0=start,
            x1=end,
            fillcolor="rgba(231, 76, 60, 0.18)",
            line_width=0,
            layer="below",
        )

    fig.add_trace(go.Scatter(x=x, y=plot_df["epsilon"], mode="lines", name="ε", line=dict(color="#8e44ad", width=2)))
    if "upper" in plot_df.columns:
        fig.add_trace(
            go.Scatter(
                x=x,
                y=plot_df["upper"],
                mode="lines",
                name=f"+{epsilon_threshold:.2f}",
                line=dict(color="#95a5a6", dash="dash", width=1),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=x,
                y=plot_df["lower"],
                mode="lines",
                name=f"−{epsilon_threshold:.2f}",
                line=dict(color="#95a5a6", dash="dash", width=1),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=x,
                y=plot_df["zero"],
                mode="lines",
                name="0",
                line=dict(color="#bdc3c7", dash="dot", width=1),
                showlegend=False,
            )
        )
    else:
        fig.add_hline(y=epsilon_threshold, line_dash="dash", line_color="#95a5a6", annotation_text=f"+{epsilon_threshold:.2f}")
        fig.add_hline(y=-epsilon_threshold, line_dash="dash", line_color="#95a5a6", annotation_text=f"−{epsilon_threshold:.2f}")
        fig.add_hline(y=0.0, line_dash="dot", line_color="#bdc3c7")

    fig.update_yaxes(title_text="ε", autorange=True)
    fig.update_xaxes(autorange=True)
    return fig


def _price_rsi_figure(
    price_df: pd.DataFrame,
    *,
    price_cols: list[str],
    rsi_df: pd.DataFrame | None,
    rsi_params: dict | None,
    currency: str,
) -> go.Figure:
    has_rsi = rsi_df is not None and not rsi_df.empty and rsi_params is not None
    if not has_rsi:
        return _time_series_figure(price_df, x="time", y=price_cols, title=None)

    period = int(rsi_params.get("rsi_period", 14))
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.62, 0.38],
        subplot_titles=(f"Price ({currency})", f"RSI ({period})"),
    )

    plot_price = price_df.copy()
    plot_price["time"] = pd.to_datetime(plot_price["time"])
    line_styles: dict[str, dict] = {
        "price": dict(line=dict(color="#2c3e50", width=2)),
        "Fair price (H₀)": dict(line=dict(color="#e67e22", dash="dash", width=1.5)),
        "Fair + perturbation (ε)": dict(line=dict(color="#3498db", dash="dot", width=1.5)),
        "Fast MA": dict(line=dict(color="#27ae60", width=1.5)),
        "Slow MA": dict(line=dict(color="#c0392b", width=1.5)),
        "Upper band (+2σ)": dict(line=dict(color="#95a5a6", dash="dash", width=1)),
        "Lower band (−2σ)": dict(line=dict(color="#95a5a6", dash="dash", width=1)),
    }
    for col in price_cols:
        if col not in plot_price.columns:
            continue
        style = line_styles.get(col, {})
        fig.add_trace(
            go.Scatter(
                x=plot_price["time"],
                y=plot_price[col],
                mode="lines",
                name=col,
                **style,
            ),
            row=1,
            col=1,
        )

    plot_rsi = rsi_df.copy()
    plot_rsi["time"] = pd.to_datetime(plot_rsi["time"])
    rsi_cols = rsi_chart_series(plot_rsi)
    rsi_styles: dict[str, dict] = {
        "RSI": dict(line=dict(color="#8e44ad", width=2)),
    }
    buy_lvl, sell_lvl, _, _ = rsi_threshold_levels(rsi_params)
    for col in rsi_cols:
        style = rsi_styles.get(col, dict(line=dict(color="#95a5a6", dash="dash", width=1)))
        fig.add_trace(
            go.Scatter(
                x=plot_rsi["time"],
                y=plot_rsi[col],
                mode="lines",
                name=col,
                **style,
            ),
            row=2,
            col=1,
        )

    if rsi_params.get("rsi_mode") == "mean_reversion":
        fig.add_hrect(y0=0, y1=buy_lvl, fillcolor="rgba(39, 174, 96, 0.08)", line_width=0, row=2, col=1)
        fig.add_hrect(y0=sell_lvl, y1=100, fillcolor="rgba(231, 76, 60, 0.08)", line_width=0, row=2, col=1)

    fig.update_yaxes(title_text=currency, row=1, col=1)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=2, col=1)
    fig.update_xaxes(title_text="", row=1, col=1)
    return fig


class PlotlyRenderer(ChartRenderer):
    def render_time_series(
        self,
        df: pd.DataFrame,
        *,
        x: str,
        y: str | list[str],
        title: str | None = None,
        chart_key: str | None = None,
    ) -> None:
        if title:
            st.subheader(title)
        y_key = y if isinstance(y, str) else "-".join(y)
        _show(
            _time_series_figure(df, x=x, y=y, title=None),
            key=_chart_key("ts", chart_key or title or y_key),
        )

    def render_epsilon_chart(
        self,
        df: pd.DataFrame,
        *,
        epsilon_threshold: float,
        chart_key: str | None = None,
    ) -> None:
        plot_df = df.copy()
        if "upper" not in plot_df.columns:
            plot_df["upper"] = epsilon_threshold
            plot_df["lower"] = -epsilon_threshold
            plot_df["zero"] = 0.0
        _show(
            _epsilon_figure(plot_df, epsilon_threshold=epsilon_threshold),
            key=_chart_key("eps", chart_key or "epsilon"),
        )

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
        charts = prepare_trade_chart_frames(
            series,
            epsilon_threshold=epsilon_threshold,
            trend_enable=trend_enable,
            trend_gate_z=trend_gate_z,
            momentum_overlay=momentum_overlay,
        )

        st.subheader("ε")
        st.caption(
            f"Buy/sell band at ±{epsilon_threshold:.2f}. "
            "Red shading: regime invalid (new buys blocked)."
        )
        self.render_epsilon_chart(charts["epsilon"], epsilon_threshold=epsilon_threshold, chart_key="trade-epsilon")

        st.subheader(f"Price & RSI ({currency})")
        price_cols = price_chart_series(charts["price"])
        if rsi_chart is not None and not rsi_chart.empty and rsi_params is not None:
            st.caption(price_rsi_caption(rsi_params=rsi_params, currency=currency))
        elif len(price_cols) > 1:
            st.caption(
                "Solid: price and moving averages. Dashed: Bollinger ±2σ bands on slow MA."
            )
        _show(
            _price_rsi_figure(
                charts["price"],
                price_cols=price_cols,
                rsi_df=rsi_chart,
                rsi_params=rsi_params,
                currency=currency,
            ),
            key=_chart_key("trade", "price-rsi"),
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

    def render_allocation_bars(
        self,
        df: pd.DataFrame,
        *,
        title: str | None = None,
        chart_key: str | None = None,
    ) -> None:
        if title:
            st.subheader(title)
        if df.empty:
            st.caption("No look-through data — add fund profiles under fund_profiles/.")
            return
        plot_df = df.sort_values("weight_pct", ascending=True)
        fig = go.Figure(
            go.Bar(
                x=plot_df["weight_pct"],
                y=plot_df["category"],
                orientation="h",
                marker=dict(color="#3498db"),
            )
        )
        fig.update_layout(xaxis_title="Portfolio weight (%)", yaxis_title="")
        _show(fig, key=_chart_key("alloc", chart_key or title or "bars"))

    def render_pnl_with_trades(self, df: pd.DataFrame, *, chart_key: str | None = None) -> None:
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
        _show(fig, key=_chart_key("pnl", chart_key or "trades"))
