"""Streamlit trading console — Wallet, Backtest, Trade, Recommendations tabs."""

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
from funtrade.ui.plotting import get_chart_renderer
from funtrade.ui.service import (
    CHART_WINDOW_BACKTEST_TEST,
    CHART_WINDOW_RECENT,
    H0_SOURCE_SAVED,
    H0_SOURCE_WALK_FORWARD,
    active_equilibrium_status,
    default_ui_params,
    fetch_recommendations,
    fetch_portfolio_allocation,
    momentum_trade_context,
    MODEL_AUTO,
    MODEL_MOMENTUM_BENCHMARK,
    MODEL_PERTURBATION,
    RECOMMENDATION_MODELS,
    perturbation_context,
    run_backtest_for_ui,
    backtest_params_fingerprint,
    backtest_cache_key,
    backtest_data_revision,
    buy_hold_display,
    run_refresh,
    slice_perturbation_for_chart,
    watchlist_with_class,
    DEFAULT_REFRESH_DAYS,
)

# Emoji page_icon creates /images/<hash>.png URLs that iOS/link previews open directly;
# Streamlit then serves HTML at that path and relative ./static/ assets break → blank page.
st.set_page_config(page_title="FunTrade Console", layout="wide")

_REC_BUY_ROW = "background-color: #dcfce7; color: #166534"
_REC_SELL_ROW = "background-color: #ffedd5; color: #c2410c"
# ~35px per row + header — fits 25 watchlist symbols without inner scroll for typical lists.
_RECOMMENDATIONS_TABLE_HEIGHT = 35 * 25 + 40


def _recommendation_row_styles(row: pd.Series) -> list[str]:
    action = row.get("Action")
    if action == "BUY":
        css = _REC_BUY_ROW
    elif action == "SELL":
        css = _REC_SELL_ROW
    else:
        return [""] * len(row)
    return [css] * len(row)


def _backtest_views() -> dict:
    if "backtest_views" not in st.session_state:
        st.session_state.backtest_views = {}
    return st.session_state.backtest_views


def _clear_backtest_views() -> None:
    st.session_state.pop("backtest_views", None)
    st.session_state.pop("backtest_view", None)


def _store_backtest_view(view: dict) -> None:
    symbol = view.get("symbol")
    if not symbol:
        return
    st.session_state.backtest_run_id = st.session_state.get("backtest_run_id", 0) + 1
    stored = dict(view)
    stored["run_id"] = st.session_state.backtest_run_id
    for key in (
        "equity_curve",
        "pnl_curve",
        "epsilon",
        "trade_chart",
        "price_chart",
        "model_comparison_chart",
        "model_position_chart",
        "regime_timeline_chart",
    ):
        df = stored.get(key)
        if isinstance(df, pd.DataFrame):
            stored[key] = df.copy(deep=True)
    _backtest_views()[symbol] = stored
    st.session_state.pop("backtest_view", None)


def _backtest_stat(col, label: str, value: str) -> None:
    """Markdown stat cell — avoids st.metric widget reuse across reruns."""
    with col:
        st.caption(label)
        st.markdown(f"**{value}**")

settings = Settings.from_env()
chart_renderer = get_chart_renderer(settings=settings)
if "params" not in st.session_state:
    st.session_state.params = default_ui_params(settings.watchlist[0] if settings.watchlist else "VWCE.DE")

params = st.session_state.params
_migrate: dict = {}
if not hasattr(params, "h0_weight_oil"):
    _migrate.update(
        h0_weight_oil=settings.h0_weight_oil,
        h0_weight_climate=settings.h0_weight_climate,
        trend_epsilon_weight=settings.trend_epsilon_weight,
        trend_fair_value_weight=settings.trend_fair_value_weight,
        trend_gate_sells=settings.trend_gate_sells,
        trend_gate_z=settings.trend_gate_z,
    )
if not hasattr(params, "h0_source"):
    _migrate.update(h0_source=H0_SOURCE_SAVED, epsilon_chart_window=CHART_WINDOW_RECENT)
if not hasattr(params, "paper_trade_slice_pct"):
    _p = default_ui_params(params.symbol)
    _migrate.update(paper_trade_slice_pct=_p.paper_trade_slice_pct)
if not hasattr(params, "regime_spike_sigma"):
    _p = default_ui_params(params.symbol)
    _migrate.update(
        regime_spike_sigma=_p.regime_spike_sigma,
        regime_consecutive_bars=_p.regime_consecutive_bars,
    )
if not hasattr(params, "backtest_position_limit_shares"):
    _p = default_ui_params(params.symbol)
    _migrate.update(backtest_position_limit_shares=_p.backtest_position_limit_shares)
if _migrate:
    params = replace(params, **_migrate)
    st.session_state.params = params

st.sidebar.title("FunTrade")
_watchlist = watchlist_with_class(settings)
_watch_symbols = [sym for sym, _ in _watchlist]
if params.symbol not in _watch_symbols and _watch_symbols:
    params.symbol = _watch_symbols[0]
_chosen = st.sidebar.selectbox(
    "Symbol",
    _watch_symbols,
    index=_watch_symbols.index(params.symbol) if params.symbol in _watch_symbols else 0,
    format_func=lambda s: f"{s} ({settings.for_symbol(s).asset_class or 'etf'})",
)
if _chosen != params.symbol:
    _prev = params
    params = default_ui_params(_chosen)
    params = replace(
        params,
        paper_initial_cash=_prev.paper_initial_cash,
        paper_trade_slice_pct=_prev.paper_trade_slice_pct,
        paper_fee_bps=_prev.paper_fee_bps,
        paper_position_limit_shares=_prev.paper_position_limit_shares,
        backtest_position_limit_shares=_prev.backtest_position_limit_shares,
        h0_weight_oil=_prev.h0_weight_oil,
        h0_weight_climate=_prev.h0_weight_climate,
        h0_source=_prev.h0_source,
        epsilon_chart_window=_prev.epsilon_chart_window,
    )
    st.session_state.params = params
    _clear_backtest_views()
params.symbol = _chosen
_asset_class = settings.for_symbol(params.symbol).asset_class or "etf"
st.sidebar.caption(f"Asset class: **{_asset_class.replace('_', ' ')}** (from config.json)")
params.epsilon_threshold = st.sidebar.slider("ε threshold", 0.3, 3.0, float(params.epsilon_threshold), 0.05)
st.sidebar.markdown("**H₁ blend weights**")
params.w_return = st.sidebar.slider(
    "w_return",
    0.0,
    1.0,
    float(params.w_return),
    0.05,
    help="Weight on price vs H₀ fair value — main dial for mean-reversion sensitivity to pullbacks.",
)
params.w_volume = st.sidebar.slider(
    "w_volume",
    0.0,
    1.0,
    float(params.w_volume),
    0.05,
    help="Weight on unusual volume vs 20-day baseline — stress days can push ε past the band sooner.",
)
params.w_rel_strength = st.sidebar.slider(
    "w_rel_strength",
    0.0,
    1.0,
    float(params.w_rel_strength),
    0.05,
    help="Weight on return vs sector/benchmark ETF — more buy signal when the symbol lags its peer.",
)

st.sidebar.markdown("**Regime gate**")
params.regime_spike_sigma = st.sidebar.slider(
    "Regime spike σ",
    1.5,
    5.0,
    float(params.regime_spike_sigma),
    0.1,
    help="|ε| must exceed this on consecutive days to flag stress and block new buys.",
)
params.regime_consecutive_bars = int(
    st.sidebar.slider(
        "Regime consecutive bars",
        1,
        10,
        int(params.regime_consecutive_bars),
        1,
        help="How many consecutive spike days before regime_valid becomes false.",
    )
)

st.sidebar.markdown("**Backtest wallet**")
params.backtest_position_limit_shares = st.sidebar.number_input(
    "Max shares (backtest)",
    min_value=100.0,
    max_value=50000.0,
    value=float(params.backtest_position_limit_shares),
    step=100.0,
    help="Maximum position size per symbol in the walk-forward backtest (separate from paper wallet).",
)

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

st.sidebar.markdown("**ε chart alignment**")
_h0_labels = {
    H0_SOURCE_SAVED: "Saved H₀ (DB / Trade)",
    H0_SOURCE_WALK_FORWARD: "Walk-forward H₀ (train 70%)",
}
params.h0_source = st.sidebar.radio(
    "H₀ source",
    options=[H0_SOURCE_SAVED, H0_SOURCE_WALK_FORWARD],
    format_func=lambda k: _h0_labels[k],
    index=0 if params.h0_source == H0_SOURCE_SAVED else 1,
    help="Use the same source on both tabs to compare ε apples-to-apples.",
)
_chart_labels = {
    CHART_WINDOW_RECENT: "Last 120 days",
    CHART_WINDOW_BACKTEST_TEST: "Backtest test period (~30%)",
}
params.epsilon_chart_window = st.sidebar.radio(
    "ε chart window",
    options=[CHART_WINDOW_RECENT, CHART_WINDOW_BACKTEST_TEST],
    format_func=lambda k: _chart_labels[k],
    index=0 if params.epsilon_chart_window == CHART_WINDOW_RECENT else 1,
    help="Backtest always simulates on the test slice; this controls the Trade chart range.",
)

st.sidebar.markdown("**Data refresh**")
_refresh_days = st.sidebar.number_input(
    "Refresh window (days)",
    min_value=7,
    max_value=90,
    value=int(st.session_state.get("refresh_days", DEFAULT_REFRESH_DAYS)),
    step=1,
    help="Matches make refresh REFRESH_DAYS — recent bars and factors to ingest.",
)
st.session_state.refresh_days = _refresh_days
if st.sidebar.button(
    "Run refresh (ingest → detect → paper)",
    type="primary",
    help="Same as `make refresh`. Needs network; may take a few minutes.",
):
    with st.spinner(f"Refreshing last {_refresh_days} days…"):
        try:
            result = run_refresh(
                days=_refresh_days,
                settings=params.to_settings(),
                paper=params.to_paper_settings(),
            )
            st.session_state.refresh_result = result
            st.session_state.pop("recommendations_df", None)
            _clear_backtest_views()
        except Exception as exc:
            st.session_state.refresh_result = {"ok": False, "error": str(exc), "days": _refresh_days}
    st.rerun()

if "refresh_result" in st.session_state:
    _rr = st.session_state.refresh_result
    if _rr.get("error"):
        st.sidebar.error(f"Refresh failed: {_rr['error']}")
    elif _rr.get("ok"):
        _steps = _rr.get("steps", {})
        _ingest = _steps.get("ingest", {})
        _detect = _steps.get("detect", {})
        _paper = _steps.get("paper", {})
        st.sidebar.success(
            f"Refresh done ({_rr.get('days', '?')}d): "
            f"{_ingest.get('total_rows', 0)} price rows, "
            f"{_detect.get('symbols', 0)} ε updates, "
            f"{_paper.get('fills', 0)} paper fills."
        )
    else:
        st.sidebar.warning("Refresh incomplete — see step errors below.")
        for name, step in _rr.get("steps", {}).items():
            if not step.get("ok"):
                st.sidebar.caption(f"{name}: {step.get('error', 'failed')}")

st.session_state.params = params

tab_wallet, tab_portfolio, tab_backtest, tab_trade, tab_recommendations = st.tabs(
    ["Wallet", "Portfolio", "Backtest", "Trade", "Recommendations"]
)

with tab_wallet:
    st.subheader("Paper Portfolio")
    summary = get_portfolio_summary(settings=params.to_settings(), paper=params.to_paper_settings())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cash (EUR)", f"{summary['cash_eur']:,.2f}")
    c2.metric("Realized PnL", f"{summary['realized_pnl']:,.2f}")
    c3.metric("Unrealized PnL", f"{summary['unrealized_pnl']:,.2f}")
    c4.metric("Total PnL", f"{summary['total_pnl']:,.2f}")

    if summary["positions"]:
        st.dataframe(pd.DataFrame(summary["positions"]), width="stretch")
    else:
        st.info("No open positions.")

    trades = load_recent_trades(limit=20, settings=params.to_settings())
    if not trades.empty:
        st.subheader("Recent Trades")
        st.dataframe(trades, width="stretch")

    if st.button("Reset paper portfolio"):
        reset_paper_portfolio(paper=params.to_paper_settings(), settings=params.to_settings())
        st.success("Portfolio reset.")
        st.rerun()

with tab_portfolio:
    st.subheader("Portfolio allocation")
    st.caption(
        "Strategic holdings from **`portfolio.json`** with look-through sector/region/asset-class "
        "from **`fund_profiles/`**. Separate from the paper trading wallet."
    )
    alloc = fetch_portfolio_allocation()
    if alloc is None:
        st.info(
            "No **`portfolio.json`** found. Run `cp portfolio.json.example portfolio.json` "
            "(or `make setup`) and enter your Nordnet weights."
        )
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Holdings", len(alloc.holdings))
        c2.metric("Listed weight", f"{alloc.total_weight_pct:.1f}%")
        c3.metric("With profile", int(alloc.holdings["has_profile"].sum()))
        c4.metric("Uncovered", f"{alloc.uncovered_weight_pct:.1f}%")
        if alloc.weight_is_normalized:
            st.caption(
                f"Weights in **{alloc.name}** sum to {alloc.total_weight_pct:.1f}% — "
                "look-through uses normalized proportions."
            )
        if alloc.missing_profiles:
            st.warning(
                "Missing fund profiles for: "
                + ", ".join(f"`{s}`" for s in alloc.missing_profiles)
                + " — add `fund_profiles/{symbol}.json` or remove from portfolio."
            )

        holdings = alloc.holdings.rename(
            columns={
                "symbol": "Symbol",
                "portfolio_weight_pct": "Weight %",
                "name": "Fund",
                "profile_as_of": "Profile as of",
                "has_profile": "Profile",
                "note": "Note",
            }
        )
        show_holdings = [
            c for c in holdings.columns
            if c not in ("weight_pct", "value_eur", "shares")
        ]
        st.markdown("**Holdings**")
        st.dataframe(holdings[show_holdings], width="stretch", hide_index=True)

        col_geo, col_sec = st.columns(2)
        with col_geo:
            chart_renderer.render_allocation_bars(
                alloc.regions,
                title="Geography (look-through)",
                chart_key="portfolio-regions",
            )
            if not alloc.regions.empty:
                st.dataframe(alloc.regions, width="stretch", hide_index=True)
        with col_sec:
            chart_renderer.render_allocation_bars(
                alloc.sectors,
                title="Sectors (look-through)",
                chart_key="portfolio-sectors",
            )
            if not alloc.sectors.empty:
                st.dataframe(alloc.sectors, width="stretch", hide_index=True)

        st.markdown("**Asset class (look-through)**")
        chart_renderer.render_allocation_bars(
            alloc.asset_classes,
            title=None,
            chart_key="portfolio-asset-class",
        )
        if not alloc.asset_classes.empty:
            st.dataframe(alloc.asset_classes, width="stretch", hide_index=True)

with tab_backtest:
    st.subheader(f"Backtest — {params.symbol}")
    h0 = active_equilibrium_status(
        params.symbol, h0_source=params.h0_source, settings=params.to_settings(),
    )
    if h0:
        st.caption(f"H₀ used for ε: **{h0['source']}**")
        st.json(h0)
    else:
        st.warning("No H₀ params for this source. Run calibrate first (saved mode).")

    if st.button("Run backtest", type="primary"):
        try:
            _store_backtest_view(run_backtest_for_ui(params))
        except Exception as e:
            st.error(str(e))
        else:
            st.rerun()

    view = _backtest_views().get(params.symbol)
    current_key = backtest_cache_key(params)
    if view is not None:
        if view.get("cache_key") != current_key:
            fp = backtest_params_fingerprint(params)
            if view.get("fingerprint") != fp:
                st.info(
                    "Sidebar settings changed since the last backtest — click **Run backtest** to refresh."
                )
            elif view.get("data_revision") != backtest_data_revision(
                params.symbol, settings=params.to_settings()
            ):
                st.info(
                    "Price data changed since the last backtest (ingest/refresh) — click **Run backtest**."
                )
            else:
                st.info("Backtest is out of date — click **Run backtest** to refresh.")
        else:
            rid = view.get("run_id", 0)
            chart_key = f"{params.symbol}-r{rid}"
            as_of = view.get("price_as_of")
            cap = (
                f"Backtest run **#{rid}** · simulated wallet **€{view['initial_capital_eur']:,.0f}** · "
                f"max **{view.get('position_limit_shares', params.backtest_position_limit_shares):,.0f}** shares. "
                "**Realized** = closed trades; **Unrealized** = open position mark-to-market. "
                f"Net profit = total PnL − fees (€{view.get('total_fees_eur', 0):,.2f})."
            )
            if as_of:
                cap += f" Prices through **{as_of[:10]}**."
            st.caption(cap)
            m1, m2, m3, m4 = st.columns(4)
            _backtest_stat(m1, "Realized PnL (EUR)", f"{view['realized_pnl_eur']:+,.2f}")
            _backtest_stat(m2, "Unrealized PnL (EUR)", f"{view['unrealized_pnl_eur']:+,.2f}")
            _backtest_stat(m3, "Total PnL (EUR)", f"{view['total_pnl_eur']:+,.2f}")
            _backtest_stat(
                m4,
                "Net profit (EUR)",
                f"{view['net_profit_eur']:+,.2f} ({view['return_pct']:+.2f}%)",
            )

            c1, c2, c3, c4 = st.columns(4)
            _backtest_stat(c1, "Final portfolio (EUR)", f"{view['final_portfolio_eur']:,.2f}")
            _backtest_stat(c2, "Cash at end", f"€{view['final_cash_eur']:,.2f}")
            _backtest_stat(c3, "Shares at end", f"{view['final_shares']:,.0f}")
            _backtest_stat(c4, "Trades", str(view["total_trades"]))

            c5, c6, c7 = st.columns(3)
            bh = buy_hold_display(view)
            _backtest_stat(
                c5,
                "Buy & hold (test period)",
                f"{bh['profit_eur']:+,.2f} ({bh['return_pct']:+.1f}%)",
            )
            _backtest_stat(c6, "Avg cost (if holding)", f"€{view.get('avg_cost_eur', 0):,.2f}")
            _backtest_stat(c7, "Max drawdown (EUR)", f"{view['max_drawdown']:,.2f}")
            st.caption(
                f"**Buy & hold** is a passive benchmark (not your strategy): invest **€{view['initial_capital_eur']:,.0f}** "
                f"at **€{bh['first_price']:,.2f}** on {bh['test_start_date']} and hold to **€{bh['last_price']:,.2f}** "
                f"on {bh['test_end_date']}. "
                "It does **not** depend on ε trades — with **0 strategy trades**, net profit stays **€0** "
                "while buy & hold can still be large in a rising test window."
            )

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
                if st.button(f"Use suggested threshold ({suggested:.2f})", key=f"bt-suggest-{rid}"):
                    params.epsilon_threshold = suggested
                    st.session_state.params = params
                    try:
                        _store_backtest_view(run_backtest_for_ui(params))
                    except Exception as e:
                        st.error(str(e))
                    else:
                        st.rerun()

            if isinstance(view.get("trade_chart"), pd.DataFrame) and not view["trade_chart"].empty:
                h0_label = "saved" if view.get("h0_source") == H0_SOURCE_SAVED else "walk-forward"
                st.subheader("ε")
                st.caption(
                    f"ε chart: **test period** from **{h0_label}** H₀ "
                    f"(from {view.get('test_start', '?')}). "
                    f"Regime gate: spike σ **{view.get('regime_spike_sigma', params.regime_spike_sigma):.1f}**, "
                    f"**{view.get('regime_consecutive_bars', params.regime_consecutive_bars)}** consecutive bars "
                    f"({view.get('regime_invalid_days', 0)} invalid days). "
                    "Red shading = **regime invalid** (new buys blocked). "
                    "Match Trade tab: same H₀ source + “Backtest test period” window."
                )
                chart_renderer.render_epsilon_chart(
                    view["trade_chart"],
                    epsilon_threshold=view.get("epsilon_threshold", params.epsilon_threshold),
                    chart_key=f"{chart_key}-epsilon",
                )
            if isinstance(view.get("price_chart"), pd.DataFrame) and not view["price_chart"].empty:
                chart_renderer.render_time_series(
                    view["price_chart"],
                    x="time",
                    y="price",
                    title=f"Price ({settings.currency}) — test period",
                    chart_key=f"{chart_key}-price",
                )
            if isinstance(view.get("pnl_curve"), pd.DataFrame) and not view["pnl_curve"].empty:
                st.subheader("Realized vs unrealized PnL")
                chart_renderer.render_pnl_with_trades(view["pnl_curve"], chart_key=f"{chart_key}-pnl")
                traded = view["pnl_curve"]
                if {"shares_bought", "shares_sold"}.issubset(traded.columns):
                    total_bought = float(traded["shares_bought"].sum())
                    total_sold = float(traded["shares_sold"].sum())
                    st.caption(
                        f"Test period: **{total_bought:,.0f}** shares bought, **{total_sold:,.0f}** shares sold "
                        f"(bars on secondary axis)."
                    )
            if isinstance(view.get("equity_curve"), pd.DataFrame) and not view["equity_curve"].empty:
                chart_renderer.render_time_series(
                    view["equity_curve"],
                    x="time",
                    y="portfolio_eur",
                    title="Portfolio value over test period (Perturbation)",
                    chart_key=f"{chart_key}-equity",
                )

            comp = view.get("model_comparison_chart")
            if isinstance(comp, pd.DataFrame) and not comp.empty:
                st.subheader("Model comparison")
                mom = view.get("momentum_benchmark") or {}
                mixed = view.get("mixed_benchmark") or {}
                c1, c2, c3, c4 = st.columns(4)
                _backtest_stat(
                    c1,
                    "Perturbation net profit",
                    f"{view['net_profit_eur']:+,.2f} ({view['return_pct']:+.2f}%)",
                )
                if mom.get("error"):
                    _backtest_stat(c2, "Momentum benchmark", "—")
                    st.caption(f"Momentum backtest skipped: {mom['error']}")
                else:
                    _backtest_stat(
                        c2,
                        "Momentum net profit",
                        f"{mom.get('net_profit_eur', 0):+,.2f} ({mom.get('return_pct', 0):+.2f}%)",
                    )
                if mixed.get("error"):
                    _backtest_stat(c3, "Mixed (auto)", "—")
                elif mixed.get("net_profit_eur") is not None:
                    _backtest_stat(
                        c3,
                        "Mixed (auto) net profit",
                        f"{mixed.get('net_profit_eur', 0):+,.2f} ({mixed.get('return_pct', 0):+.2f}%)",
                    )
                else:
                    _backtest_stat(c3, "Mixed (auto)", "—")
                _backtest_stat(c4, "Mixed Sharpe", f"{mixed.get('sharpe', 0):.2f}" if mixed.get("sharpe") is not None else "—")
                st.caption(
                    "Same test window and wallet cap. **Mixed (auto)** routes each day via "
                    "`strategy_router` in config.json: trending → momentum, ranging → perturbation."
                )
                compare_y = [c for c in comp.columns if c != "time"]
                chart_renderer.render_time_series(
                    comp,
                    x="time",
                    y=compare_y,
                    title="Perturbation vs momentum vs mixed (auto) — portfolio value",
                    chart_key=f"{chart_key}-model-compare",
                )
                pos_chart = view.get("model_position_chart")
                if isinstance(pos_chart, pd.DataFrame) and not pos_chart.empty:
                    pos_y = [c for c in pos_chart.columns if c != "time"]
                    chart_renderer.render_time_series(
                        pos_chart,
                        x="time",
                        y=pos_y,
                        title="Model comparison — shares held",
                        chart_key=f"{chart_key}-model-position",
                    )
                    st.caption(
                        "Exposure path over the test window. "
                        f"Max position cap: **{view.get('position_limit_shares', params.backtest_position_limit_shares):,.0f}** shares."
                    )
                regime_chart = view.get("regime_timeline_chart")
                if isinstance(regime_chart, pd.DataFrame) and not regime_chart.empty:
                    st.caption("Mixed strategy regime labels (trending / ranging / uncertain) over the test window.")
                    st.dataframe(
                        regime_chart.tail(30),
                        width="stretch",
                        hide_index=True,
                    )
                ma_chart = mom.get("ma_chart") if isinstance(mom, dict) else None
                if isinstance(ma_chart, pd.DataFrame) and not ma_chart.empty:
                    chart_renderer.render_time_series(
                        ma_chart,
                        x="time",
                        y=["price", "Fast MA", "Slow MA"],
                        title="Momentum benchmark — price and moving averages",
                        chart_key=f"{chart_key}-momentum-ma",
                    )

with tab_trade:
    st.subheader(f"Trade — {params.symbol}")
    if st.button("Calibrate H₀"):
        try:
            model = calibrate_equilibrium(params.symbol, settings=params.to_settings())
            st.success(f"Calibrated: half-life {model.half_life_days:.1f} days")
        except Exception as e:
            st.error(str(e))

    trade_summary = get_portfolio_summary(
        settings=params.to_settings(), paper=params.to_paper_settings(),
    )
    trade_pos_qty = 0.0
    for pos in trade_summary.get("positions", []):
        if pos["symbol"] == params.symbol:
            trade_pos_qty = float(pos["net_qty_shares"])
    mom_ctx = momentum_trade_context(
        params.symbol,
        settings=params.to_settings(),
        window=params.epsilon_chart_window,
        current_position=trade_pos_qty,
    )

    series = perturbation_context(
        params.symbol,
        weights=params.perturbation_weights(),
        settings=params.to_settings(),
        h0_source=params.h0_source,
    )
    st.markdown("**Perturbation (ε mean reversion)**")
    if not series.empty:
        chart_series = slice_perturbation_for_chart(
            series,
            symbol=params.symbol,
            window=params.epsilon_chart_window,
            settings=params.to_settings(),
        )
        h0 = active_equilibrium_status(
            params.symbol, h0_source=params.h0_source, settings=params.to_settings(),
        )
        if h0:
            st.caption(f"H₀ for ε: **{h0['source']}** · chart: **{_chart_labels[params.epsilon_chart_window]}**")

        latest = chart_series.iloc[-1]
        p1, p2, p3 = st.columns(3)
        p1.metric("ε", f"{latest['epsilon']:.3f}")
        p2.metric("Regime valid", "Yes" if latest["regime_valid"] else "No")
        if settings.trend_enable:
            p3.metric("z_trend", f"{latest.get('z_trend', 0.0):.2f}")
    else:
        st.caption("No perturbation series available for this symbol.")
        chart_series = pd.DataFrame()

    st.markdown("**Momentum benchmark**")
    if mom_ctx.get("error"):
        st.caption(mom_ctx["error"])
        momentum_overlay = None
    else:
        st.caption(
            f"Fast **{mom_ctx['fast_ma_days']}d** / slow **{mom_ctx['slow_ma_days']}d** MA · "
            f"**{mom_ctx['momentum_lookback_days']}d** momentum"
        )
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Fast MA", f"{mom_ctx['fast_ma']:.2f}")
        m2.metric("Slow MA", f"{mom_ctx['slow_ma']:.2f}")
        m3.metric("Momentum", f"{mom_ctx['momentum_pct']:.1f}%" if mom_ctx["momentum_pct"] is not None else "n/a")
        m4.metric("Fast > slow", "Yes" if mom_ctx["ma_bullish"] else "No")
        m5.metric("Signal", mom_ctx["action"])
        momentum_overlay = mom_ctx.get("price_overlay")

    if not chart_series.empty:
        chart_renderer.render_trade_charts(
            chart_series,
            epsilon_threshold=params.epsilon_threshold,
            currency=settings.currency,
            trend_enable=settings.trend_enable,
            trend_gate_z=params.trend_gate_z if params.trend_gate_sells else None,
            momentum_overlay=momentum_overlay,
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
                    epsilon=p.epsilon,
                    epsilon_threshold=params.epsilon_threshold,
                    regime_valid=p.regime_valid,
                    paper=params.to_paper_settings(), settings=params.to_settings(),
                )
                if fill:
                    st.success(f"{fill.side} {fill.qty_shares} @ {fill.price:.2f}")
                else:
                    st.info("No fill (limits or flat signal).")
            else:
                st.info(f"Signal {sig} — no trade executed.")

with tab_recommendations:
    st.subheader("Recommendations")
    _model_labels = {
        MODEL_PERTURBATION: "Perturbation (ε mean reversion)",
        MODEL_MOMENTUM_BENCHMARK: "Momentum benchmark (MA crossover)",
        MODEL_AUTO: "Auto (regime router)",
    }
    rec_model = st.radio(
        "Model",
        options=list(RECOMMENDATION_MODELS),
        format_func=lambda k: _model_labels[k],
        horizontal=True,
        key="rec_model",
    )
    prev_model = st.session_state.get("rec_model_prev", MODEL_PERTURBATION)
    if rec_model != prev_model:
        st.session_state.rec_model_prev = rec_model
        st.session_state.pop("recommendations_df", None)

    if rec_model == MODEL_PERTURBATION:
        st.caption(
            "Reads latest **ε** and **regime_valid** from the database (last detect/refresh), "
            "then applies each asset class’s threshold — no full recompute. "
            "Use sidebar **Run refresh** to update ε. Act on **BUY** / **SELL** only."
        )
    elif rec_model == MODEL_AUTO:
        st.caption(
            "Per-symbol **regime router** (`strategy_router` in config.json): "
            "**trending** → momentum (MA crossover); **ranging** → perturbation (ε mean-reversion). "
            "Run **Run refresh** / `make detect` to persist regime labels."
        )
    else:
        st.caption(
            "Dual **MA crossover** benchmark: **buy** when fast MA > slow MA and 63-day momentum is positive; "
            "**sell** on crossunder when long. “Fast > slow” is **not** price vs MA — price can sit above both in a rally. "
            "Settings: `config.json` → `momentum_benchmark`."
        )
    assume_holding_all = st.toggle(
        "Assume I hold every symbol",
        value=bool(st.session_state.get("rec_assume_holding_all", False)),
        help="Treat each watchlist symbol as a long position (for your DNB portfolio). "
        "Enables SELL / trend-gate notes when ε is high. "
        "Momentum (scale mode): still BUY = add another slice while trend is up.",
    )
    prev_assume = st.session_state.get("rec_assume_holding_all", False)
    if assume_holding_all != prev_assume:
        st.session_state.rec_assume_holding_all = assume_holding_all

    if st.button("Refresh recommendations", type="primary") or assume_holding_all != prev_assume or rec_model != prev_model:
        try:
            st.session_state.recommendations_df = fetch_recommendations(
                params, assume_holding_all=assume_holding_all, model=rec_model,
            )
        except Exception as e:
            st.error(str(e))

    if "recommendations_df" in st.session_state:
        rec = st.session_state.recommendations_df
        if rec.empty:
            st.warning("Watchlist is empty. Add symbols under `etf`, `mutual_fund`, or `share` in config.json.")
        else:
            if rec.attrs.get("assume_holding_all"):
                st.info(
                    "Showing signals **as if you hold each symbol** (paper qty where flat). "
                    "Rows marked *assumed* are not in the paper wallet."
                )
            n_buy = int((rec["action"] == "BUY").sum()) if "action" in rec.columns else 0
            n_sell = int((rec["action"] == "SELL").sum()) if "action" in rec.columns else 0
            n_hold = int((rec["action"] == "HOLD").sum()) if "action" in rec.columns else 0
            m1, m2, m3 = st.columns(3)
            m1.metric("Buy", n_buy)
            m2.metric("Sell", n_sell)
            m3.metric("Hold", n_hold)

            display = rec.copy()
            if "position_assumed" in display.columns:
                display["position_label"] = display.apply(
                    lambda r: f"{r['position_shares']:.0f}*" if r.get("position_assumed") else f"{r['position_shares']:.0f}",
                    axis=1,
                )
            else:
                display["position_label"] = display["position_shares"].map(lambda x: f"{x:.0f}")

            display = display.rename(
                columns={
                    "symbol": "Symbol",
                    "asset_class": "Class",
                    "as_of": "As of",
                    "price": "Price",
                    "epsilon": "ε",
                    "threshold": "ε thresh",
                    "regime_valid": "Regime OK",
                    "z_trend": "z_trend",
                    "market_regime": "Regime",
                    "selected_model": "Strategy",
                    "fast_ma": "Fast MA",
                    "slow_ma": "Slow MA",
                    "momentum_pct": "Momentum %",
                    "ma_bullish": "Fast > slow",
                    "position_label": "Position",
                    "action": "Action",
                    "note": "Note",
                }
            )
            show_cols = [c for c in display.columns if c not in ("signal", "position_shares", "position_assumed")]
            if rec_model == MODEL_MOMENTUM_BENCHMARK:
                show_cols = [
                    c for c in show_cols
                    if c not in ("ε", "ε thresh", "Regime OK", "z_trend", "Regime", "Strategy")
                ]
            elif rec_model == MODEL_AUTO:
                pass
            else:
                show_cols = [
                    c for c in show_cols
                    if c not in ("Fast MA", "Slow MA", "Momentum %", "Fast > slow", "Regime", "Strategy")
                ]
            table = display[show_cols]
            st.dataframe(
                table.style.apply(_recommendation_row_styles, axis=1),
                width="stretch",
                height=_RECOMMENDATIONS_TABLE_HEIGHT,
                hide_index=True,
                column_config={
                    "Price": st.column_config.NumberColumn(format="%.2f"),
                    "ε": st.column_config.NumberColumn(format="%.3f"),
                    "ε thresh": st.column_config.NumberColumn(format="%.2f"),
                    "Regime OK": st.column_config.CheckboxColumn(),
                    "Fast MA": st.column_config.NumberColumn(format="%.2f"),
                    "Slow MA": st.column_config.NumberColumn(format="%.2f"),
                    "Momentum %": st.column_config.NumberColumn(format="%.1f"),
                    "Fast > slow": st.column_config.CheckboxColumn(),
                },
            )
            if rec.attrs.get("assume_holding_all"):
                st.caption("*Assumed holding (not in paper wallet). Qty ≈ one trade slice (PAPER_TRADE_SLICE_PCT).")
            detected_at = rec.attrs.get("detected_at")
            if detected_at and rec.attrs.get("model") in (MODEL_PERTURBATION, MODEL_AUTO):
                st.caption(f"ε snapshot from detect run at **{detected_at}** UTC.")
            errors = rec.attrs.get("errors", [])
            if errors:
                st.warning(
                    f"No persisted ε for: {', '.join(errors)} — run sidebar **Run refresh** or `make detect`."
                )
    else:
        st.info("Click **Refresh recommendations** to load the watchlist.")
