"""Streamlit native charts (st.line_chart + matplotlib PnL)."""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from funtrade.ui.plotting.base import ChartRenderer
from funtrade.ui.plotting.data import prepare_trade_chart_frames


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
    ) -> None:
        if title:
            st.subheader(title)
        st.line_chart(df, x=x, y=y)

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
        st.line_chart(charts["epsilon"], x="time", y=["epsilon", "upper", "lower", "zero"])

        st.subheader(f"Price ({currency})")
        st.line_chart(charts["price"], x="time", y="price")

        if "z_trend" in charts:
            st.subheader("Trend (z_trend)")
            z_cols = ["z_trend"]
            if "gate" in charts["z_trend"].columns:
                z_cols.extend(["gate", "neg_gate"])
            st.line_chart(charts["z_trend"], x="time", y=z_cols)

    def render_pnl_with_trades(self, df: pd.DataFrame) -> None:
        st.pyplot(_pnl_with_trade_shares_figure(df), clear_figure=True)
