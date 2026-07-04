"""Matplotlib charts for the Streamlit console."""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd


def pnl_with_trade_shares_chart(df: pd.DataFrame) -> plt.Figure:
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


def price_epsilon_chart(
    df: pd.DataFrame,
    *,
    epsilon_threshold: float | None = None,
    price_label: str = "Price",
) -> plt.Figure:
    """Price and ε on separate y-axes (avoids scale mismatch on a single axis)."""
    plot_df = df.copy()
    if "time" in plot_df.columns:
        plot_df = plot_df.set_index("time")
    plot_df.index = pd.to_datetime(plot_df.index)

    fig, ax_eps = plt.subplots(figsize=(10, 4))
    ax_eps.plot(plot_df.index, plot_df["epsilon"], label="ε", color="#9b59b6", linewidth=1.5)
    ax_eps.axhline(0, color="#666666", linewidth=0.8, alpha=0.5)
    if epsilon_threshold is not None and epsilon_threshold > 0:
        ax_eps.axhline(epsilon_threshold, color="#e74c3c", linewidth=0.9, linestyle="--", alpha=0.7, label=f"+ε {epsilon_threshold:.2f}")
        ax_eps.axhline(-epsilon_threshold, color="#27ae60", linewidth=0.9, linestyle="--", alpha=0.7, label=f"−ε {epsilon_threshold:.2f}")
    ax_eps.set_ylabel("ε")
    ax_eps.grid(True, alpha=0.3)

    ax_price = ax_eps.twinx()
    ax_price.plot(plot_df.index, plot_df["price"], label=price_label, color="#34495e", linewidth=1.2, alpha=0.85)
    ax_price.set_ylabel(price_label)

    lines_eps, labels_eps = ax_eps.get_legend_handles_labels()
    lines_px, labels_px = ax_price.get_legend_handles_labels()
    ax_eps.legend(lines_eps + lines_px, labels_eps + labels_px, loc="upper left", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig
