"""Streamlit native charts (st.line_chart + matplotlib PnL)."""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from funtrade.ui.plotting.base import ChartRenderer
from funtrade.ui.plotting.data import normalize_chart_times, prepare_trade_chart_frames, regime_invalid_spans


def _epsilon_figure(df: pd.DataFrame, *, epsilon_threshold: float) -> plt.Figure:
    plot_df = df.copy()
    if plot_df.empty or "epsilon" not in plot_df.columns:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.set_ylabel("ε")
        return fig
    if "time" in plot_df.columns:
        plot_df = plot_df.set_index("time")
    plot_df.index = normalize_chart_times(pd.Series(plot_df.index))

    fig, ax = plt.subplots(figsize=(10, 4))
    for start, end in regime_invalid_spans(plot_df.reset_index(), time_col="time"):
        ax.axvspan(start, end, color="#e74c3c", alpha=0.18, linewidth=0)

    ax.plot(plot_df.index, plot_df["epsilon"], label="ε", color="#8e44ad", linewidth=1.8)
    upper = plot_df["upper"] if "upper" in plot_df.columns else epsilon_threshold
    lower = plot_df["lower"] if "lower" in plot_df.columns else -epsilon_threshold
    if isinstance(upper, (int, float)):
        ax.axhline(upper, color="#95a5a6", linestyle="--", linewidth=1, label=f"+{epsilon_threshold:.2f}")
        ax.axhline(lower, color="#95a5a6", linestyle="--", linewidth=1, label=f"−{epsilon_threshold:.2f}")
    else:
        ax.plot(plot_df.index, upper, color="#95a5a6", linestyle="--", linewidth=1, label=f"+{epsilon_threshold:.2f}")
        ax.plot(plot_df.index, lower, color="#95a5a6", linestyle="--", linewidth=1, label=f"−{epsilon_threshold:.2f}")
    ax.axhline(0.0, color="#bdc3c7", linestyle=":", linewidth=1)

    if "regime_valid" in plot_df.columns and (~plot_df["regime_valid"].fillna(True).astype(bool)).any():
        ax.plot([], [], color="#e74c3c", alpha=0.35, linewidth=8, label="Regime invalid (buys blocked)")

    ax.set_ylabel("ε")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def _pnl_with_trade_shares_figure(df: pd.DataFrame) -> plt.Figure:
    """Realized / unrealized PnL (EUR) with buy/sell share counts on a secondary axis."""
    plot_df = df.copy()
    if "time" in plot_df.columns:
        plot_df = plot_df.set_index("time")
    plot_df.index = pd.to_datetime(plot_df.index)

    fig, ax_pnl = plt.subplots(figsize=(10, 4))
    ax_pnl.plot(plot_df.index, plot_df["realized_pnl"], label="Realized PnL (EUR)", color="#2ecc71", linewidth=1.5)
    ax_pnl.plot(plot_df.index, plot_df["unrealized_pnl"], label="Unrealized PnL (EUR)", color="#3498db", linewidth=1.5)
    ax_pnl.set_ylabel("PnL (EUR)")
    ax_pnl.grid(True, alpha=0.3)

    ax_shares = ax_pnl.twinx()
    bought = plot_df["shares_bought"].fillna(0.0)
    sold = plot_df["shares_sold"].fillna(0.0)
    width = pd.Timedelta(days=0.6)
    ax_shares.bar(
        plot_df.index[bought > 0],
        bought[bought > 0],
        width=width,
        color="#27ae60",
        alpha=0.45,
        label="Shares bought",
        align="center",
    )
    ax_shares.bar(
        plot_df.index[sold > 0],
        -sold[sold > 0],
        width=width,
        color="#e74c3c",
        alpha=0.45,
        label="Shares sold",
        align="center",
    )
    ax_shares.set_ylabel("Shares traded")
    ax_shares.axhline(0, color="#666666", linewidth=0.8, alpha=0.5)

    lines_pnl, labels_pnl = ax_pnl.get_legend_handles_labels()
    lines_sh, labels_sh = ax_shares.get_legend_handles_labels()
    ax_pnl.legend(lines_pnl + lines_sh, labels_pnl + labels_sh, loc="upper left", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


class StreamlitNativeRenderer(ChartRenderer):
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
        kwargs = {"x": x, "y": y}
        if chart_key:
            kwargs["key"] = chart_key
        st.line_chart(df, **kwargs)

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
        kwargs: dict = {"clear_figure": True}
        if chart_key:
            kwargs["key"] = chart_key
        st.pyplot(_epsilon_figure(plot_df, epsilon_threshold=epsilon_threshold), **kwargs)

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
        st.caption(
            f"Buy/sell band at ±{epsilon_threshold:.2f}. "
            "Red shading: regime invalid (new buys blocked)."
        )
        self.render_epsilon_chart(charts["epsilon"], epsilon_threshold=epsilon_threshold, chart_key="trade-epsilon")

        st.subheader(f"Price ({currency})")
        st.line_chart(charts["price"], x="time", y="price")

        if "z_trend" in charts:
            st.subheader("Trend (z_trend)")
            z_cols = ["z_trend"]
            if "gate" in charts["z_trend"].columns:
                z_cols.extend(["gate", "neg_gate"])
            st.line_chart(charts["z_trend"], x="time", y=z_cols)

    def render_pnl_with_trades(self, df: pd.DataFrame, *, chart_key: str | None = None) -> None:
        kwargs: dict = {"clear_figure": True}
        if chart_key:
            kwargs["key"] = chart_key
        st.pyplot(_pnl_with_trade_shares_figure(df), **kwargs)
