"""Streamlit trading console — Wallet, Backtest, Trade tabs."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from funtrade.config import Settings
from funtrade.execution.paper import (
    execute_trade,
    get_portfolio_summary,
    load_recent_trades,
    reset_paper_portfolio,
)
from funtrade.data.market import latest_price
from funtrade.models.equilibrium import calibrate_equilibrium
from funtrade.models.perturbation import detect_latest_perturbations, signal_from_epsilon
from funtrade.ui.service import (
    default_ui_params,
    equilibrium_status,
    perturbation_context,
    run_backtest_for_ui,
)

st.set_page_config(page_title="FunTrade Console", page_icon="📈", layout="wide")

settings = Settings.from_env()
if "params" not in st.session_state:
    st.session_state.params = default_ui_params(settings.watchlist[0] if settings.watchlist else "VWCE.DE")

params = st.session_state.params

st.sidebar.title("FunTrade")
params.symbol = st.sidebar.selectbox("Symbol", settings.watchlist, index=settings.watchlist.index(params.symbol) if params.symbol in settings.watchlist else 0)
params.epsilon_threshold = st.sidebar.slider("ε threshold", 0.5, 5.0, float(params.epsilon_threshold), 0.1)
params.w_return = st.sidebar.slider("w_return", 0.0, 1.0, float(params.w_return), 0.05)
params.w_volume = st.sidebar.slider("w_volume", 0.0, 1.0, float(params.w_volume), 0.05)
params.w_rel_strength = st.sidebar.slider("w_rel_strength", 0.0, 1.0, float(params.w_rel_strength), 0.05)

tab_wallet, tab_backtest, tab_trade = st.tabs(["Wallet", "Backtest", "Trade"])

with tab_wallet:
    st.subheader("Paper Portfolio")
    summary = get_portfolio_summary(settings=params.to_settings(), paper=params.to_paper_settings())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cash (EUR)", f"{summary['cash_eur']:,.2f}")
    c2.metric("Realized PnL", f"{summary['realized_pnl']:,.2f}")
    c3.metric("Unrealized PnL", f"{summary['unrealized_pnl']:,.2f}")
    c4.metric("Total PnL", f"{summary['total_pnl']:,.2f}")

    if summary["positions"]:
        st.dataframe(pd.DataFrame(summary["positions"]), use_container_width=True)
    else:
        st.info("No open positions.")

    trades = load_recent_trades(limit=20, settings=params.to_settings())
    if not trades.empty:
        st.subheader("Recent Trades")
        st.dataframe(trades, use_container_width=True)

    if st.button("Reset paper portfolio"):
        reset_paper_portfolio(paper=params.to_paper_settings(), settings=params.to_settings())
        st.success("Portfolio reset.")
        st.rerun()

with tab_backtest:
    st.subheader(f"Backtest — {params.symbol}")
    h0 = equilibrium_status(params.symbol, settings=params.to_settings())
    if h0:
        st.json(h0)
    else:
        st.warning("No calibrated H₀ params. Run calibrate first.")

    if st.button("Run backtest"):
        try:
            view = run_backtest_for_ui(params)
            st.session_state.backtest_view = view
        except Exception as e:
            st.error(str(e))

    if "backtest_view" in st.session_state:
        view = st.session_state.backtest_view
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total return (EUR)", f"{view['total_return']:,.2f}")
        m2.metric("Sharpe", f"{view['sharpe']:.2f}")
        m3.metric("Max drawdown", f"{view['max_drawdown']:,.2f}")
        m4.metric("Trades", view["total_trades"])
        if isinstance(view.get("equity_curve"), pd.DataFrame) and not view["equity_curve"].empty:
            st.line_chart(view["equity_curve"], x="time", y="equity")

with tab_trade:
    st.subheader(f"Trade — {params.symbol}")
    if st.button("Calibrate H₀"):
        try:
            model = calibrate_equilibrium(params.symbol, settings=params.to_settings())
            st.success(f"Calibrated: half-life {model.half_life_days:.1f} days")
        except Exception as e:
            st.error(str(e))

    series = perturbation_context(
        params.symbol,
        weights=params.perturbation_weights(),
        settings=params.to_settings(),
    )
    if not series.empty:
        latest = series.iloc[-1]
        st.metric("ε", f"{latest['epsilon']:.3f}")
        st.metric("Regime valid", "Yes" if latest["regime_valid"] else "No")

        chart = series[["epsilon", "price"]].tail(120).reset_index()
        chart = chart.rename(columns={"index": "time"})
        st.line_chart(chart, x="time", y=["epsilon", "price"])

    if st.button("Run model paper cycle"):
        results = detect_latest_perturbations(symbols=[params.symbol], settings=params.to_settings())
        for p in results:
            summary = get_portfolio_summary(settings=params.to_settings(), paper=params.to_paper_settings())
            pos_qty = 0.0
            for pos in summary.get("positions", []):
                if pos["symbol"] == p.symbol:
                    pos_qty = pos["net_qty_shares"]
            sig = signal_from_epsilon(
                p.epsilon, params.epsilon_threshold, p.regime_valid,
                long_only=True, current_position=pos_qty,
            )
            price = latest_price(p.symbol, settings=params.to_settings())
            if price and sig != 0:
                fill = execute_trade(
                    sig, p.symbol, price,
                    epsilon=p.epsilon, regime_valid=p.regime_valid,
                    paper=params.to_paper_settings(), settings=params.to_settings(),
                )
                if fill:
                    st.success(f"{fill.side} {fill.qty_shares} @ {fill.price:.2f}")
                else:
                    st.info("No fill (limits or flat signal).")
            else:
                st.info(f"Signal {sig} — no trade executed.")
