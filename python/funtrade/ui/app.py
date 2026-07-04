"""Streamlit trading console — Wallet, Backtest, Trade tabs."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from dataclasses import replace

from funtrade.config import Settings
from funtrade.execution.paper import (
    execute_trade,
    get_portfolio_summary,
    load_recent_trades,
    reset_paper_portfolio,
)
from funtrade.data.market import latest_price
from funtrade.models.equilibrium import calibrate_equilibrium
from funtrade.models.perturbation import detect_latest_perturbations, signal_from_epsilon, trend_signal_kwargs
from funtrade.ui.charts import pnl_with_trade_shares_chart, price_epsilon_chart
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
if not hasattr(params, "h0_weight_oil"):
    params = replace(
        params,
        h0_weight_oil=settings.h0_weight_oil,
        h0_weight_climate=settings.h0_weight_climate,
        trend_epsilon_weight=settings.trend_epsilon_weight,
        trend_fair_value_weight=settings.trend_fair_value_weight,
        trend_gate_sells=settings.trend_gate_sells,
        trend_gate_z=settings.trend_gate_z,
    )
    st.session_state.params = params

st.sidebar.title("FunTrade")
params.symbol = st.sidebar.selectbox("Symbol", settings.watchlist, index=settings.watchlist.index(params.symbol) if params.symbol in settings.watchlist else 0)
params.epsilon_threshold = st.sidebar.slider("ε threshold", 0.3, 3.0, float(params.epsilon_threshold), 0.05)
params.w_return = st.sidebar.slider("w_return", 0.0, 1.0, float(params.w_return), 0.05)
params.w_volume = st.sidebar.slider("w_volume", 0.0, 1.0, float(params.w_volume), 0.05)
params.w_rel_strength = st.sidebar.slider("w_rel_strength", 0.0, 1.0, float(params.w_rel_strength), 0.05)

if settings.h0_enable_oil or settings.h0_enable_climate:
    st.sidebar.markdown("**H₀ macro weights**")
if settings.h0_enable_oil:
    params.h0_weight_oil = st.sidebar.slider(
        "H₀ weight oil",
        -0.30,
        0.30,
        float(params.h0_weight_oil),
        0.01,
        help="Shifts fair value with oil z-score (negative = high oil lowers equity fair value).",
    )
if settings.h0_enable_climate:
    params.h0_weight_climate = st.sidebar.slider(
        "H₀ weight climate",
        -0.30,
        0.30,
        float(params.h0_weight_climate),
        0.01,
        help="Shifts fair value with climate-transition z-score (spread or ETF proxy).",
    )

if settings.trend_enable:
    st.sidebar.markdown("**Trend expectation (H₂)**")
    params.trend_epsilon_weight = st.sidebar.slider(
        "Trend ε dampening",
        0.0,
        0.50,
        float(params.trend_epsilon_weight),
        0.01,
        help="Subtract w×z_trend from ε; uptrend lowers sell urgency.",
    )
    params.trend_fair_value_weight = st.sidebar.slider(
        "Trend fair-value lift",
        0.0,
        0.30,
        float(params.trend_fair_value_weight),
        0.01,
        help="Raises H₀ fair value when price is above its moving average.",
    )
    params.trend_gate_sells = st.sidebar.checkbox(
        "Gate sells in uptrend",
        value=bool(params.trend_gate_sells),
        help="Block exit signals when z_trend is above the gate threshold.",
    )
    params.trend_gate_z = st.sidebar.slider(
        "Trend gate z",
        0.0,
        2.0,
        float(params.trend_gate_z),
        0.05,
        disabled=not params.trend_gate_sells,
    )

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
        st.caption(
            f"Simulated wallet starting with **€{view['initial_capital_eur']:,.0f}**. "
            "**Realized** = closed trades; **Unrealized** = open position mark-to-market. "
            f"Net profit = total PnL − fees (€{view.get('total_fees_eur', 0):,.2f})."
        )
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Realized PnL (EUR)", f"{view['realized_pnl_eur']:+,.2f}")
        m2.metric("Unrealized PnL (EUR)", f"{view['unrealized_pnl_eur']:+,.2f}")
        m3.metric("Total PnL (EUR)", f"{view['total_pnl_eur']:+,.2f}")
        m4.metric("Net profit (EUR)", f"{view['net_profit_eur']:+,.2f}", f"{view['return_pct']:+.2f}%")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Final portfolio (EUR)", f"{view['final_portfolio_eur']:,.2f}")
        c2.metric("Cash at end", f"€{view['final_cash_eur']:,.2f}")
        c3.metric("Shares at end", f"{view['final_shares']:,.0f}")
        c4.metric("Trades", view["total_trades"])

        c5, c6, c7 = st.columns(3)
        c5.metric("Buy & hold profit (EUR)", f"{view['buy_and_hold_profit_eur']:+,.2f}")
        c6.metric("Avg cost (if holding)", f"€{view.get('avg_cost_eur', 0):,.2f}")
        c7.metric("Max drawdown (EUR)", f"{view['max_drawdown']:,.2f}")

        if view.get("threshold_adjusted"):
            st.info(
                f"No trades at ε **{view['requested_threshold']:.2f}** "
                f"(no buy signals on daily close). Re-ran at **{view['epsilon_threshold']:.2f}**."
            )

        if view.get("total_trades", 0) == 0:
            max_abs = view.get("epsilon_max_abs", 0.0)
            th = view.get("epsilon_threshold", params.epsilon_threshold)
            suggested = view.get("suggested_threshold", 0.75)
            blocked = view.get("buy_signals_blocked_by_regime", 0)
            msg = (
                f"No trades at ε threshold **{th:.2f}**. "
                f"Test-period max |ε| is **{max_abs:.2f}** "
                f"(buy signals: {view.get('buy_model_signals', 0)}, "
                f"sell signals: {view.get('sell_model_signals', 0)}). "
            )
            if blocked > 0:
                msg += (
                    f"**{blocked}** buy day(s) had ε below threshold but **regime_valid=false** "
                    f"(often zero volume on mutual-fund NAV feeds). "
                )
            msg += f"Daily UCITS data rarely reaches 2.0 — try **{suggested:.2f}** or lower in the sidebar."
            st.warning(msg)
            if st.button(f"Use suggested threshold ({suggested:.2f})"):
                params.epsilon_threshold = suggested
                st.session_state.params = params
                st.rerun()

        if isinstance(view.get("trade_chart"), pd.DataFrame) and not view["trade_chart"].empty:
            st.line_chart(view["trade_chart"], x="time", y="epsilon")
        if isinstance(view.get("pnl_curve"), pd.DataFrame) and not view["pnl_curve"].empty:
            st.subheader("Realized vs unrealized PnL")
            st.pyplot(pnl_with_trade_shares_chart(view["pnl_curve"]), clear_figure=True)
            traded = view["pnl_curve"]
            if {"shares_bought", "shares_sold"}.issubset(traded.columns):
                total_bought = float(traded["shares_bought"].sum())
                total_sold = float(traded["shares_sold"].sum())
                st.caption(
                    f"Test period: **{total_bought:,.0f}** shares bought, **{total_sold:,.0f}** shares sold "
                    f"(bars on secondary axis)."
                )
        if isinstance(view.get("equity_curve"), pd.DataFrame) and not view["equity_curve"].empty:
            st.subheader("Portfolio value over test period")
            st.line_chart(view["equity_curve"], x="time", y="portfolio_eur")

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
        if settings.trend_enable:
            st.metric("z_trend", f"{latest.get('z_trend', 0.0):.2f}")

        chart = series[["epsilon", "price"]].tail(120).reset_index()
        chart = chart.rename(columns={"index": "time"})
        st.pyplot(
            price_epsilon_chart(
                chart,
                epsilon_threshold=params.epsilon_threshold,
                price_label=f"Price ({settings.currency})",
            ),
            clear_figure=True,
        )

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
                **trend_signal_kwargs(params.to_settings(), float(p.inputs.get("z_trend", 0.0))),
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
