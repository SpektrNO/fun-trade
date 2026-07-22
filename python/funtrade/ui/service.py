"""Helpers for the Streamlit trading console."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

from funtrade.backtest.engine import (
    H0_SOURCE_SAVED,
    H0_SOURCE_WALK_FORWARD,
    backtest_train_test_split,
    buy_and_hold_from_prices,
    resolve_h0_equilibrium,
    run_backtest,
    run_mixed_backtest,
    run_momentum_backtest,
)
from funtrade.config import Settings
from funtrade.data.fx import convert_currency_value
from funtrade.data.factors import ingest_macro_factors
from funtrade.data.ingest import ingest_watchlist
from funtrade.data.loader import (
    MARKET_ADJ_CLOSE,
    load_latest_equilibrium_params,
    load_latest_perturbation_snapshots,
    load_price_bars,
)
from funtrade.execution.paper import PaperSettings, get_portfolio_summary, get_position_quantities
from funtrade.models.momentum import (
    compute_momentum_series,
    detect_latest_momentum,
    momentum_backtest_signal,
)
from funtrade.models.regime_router import classify_latest_regime
from funtrade.models.perturbation import (
    compute_perturbation_series,
    detect_latest_perturbations,
    signal_from_epsilon,
    trend_signal_kwargs,
)
from funtrade.portfolio.allocation import PortfolioAllocationResult, compute_portfolio_allocation
from funtrade.portfolio.overlay import build_portfolio_overlay
from funtrade.portfolio.performance import (
    PortfolioPerformanceResult,
    compute_portfolio_performance,
    default_base_date,
)
from funtrade.portfolio.values import portfolio_holding_values, portfolio_weight_pcts
from funtrade.portfolio_config import load_portfolio_config
from funtrade.ui.plotting.data import build_momentum_price_overlay, build_rsi_chart_frame

CHART_WINDOW_RECENT = "recent_120"
CHART_WINDOW_BACKTEST_TEST = "backtest_test"

MODEL_PERTURBATION = "perturbation"
MODEL_MOMENTUM_BENCHMARK = "momentum_benchmark"
MODEL_AUTO = "auto"
RECOMMENDATION_MODELS = (MODEL_PERTURBATION, MODEL_MOMENTUM_BENCHMARK, MODEL_AUTO)


from funtrade.data.fx import convert_currency_value

def backtest_params_fingerprint(params: UiParams) -> str:
    """Stable id for sidebar settings that affect backtest output."""
    w = params.perturbation_weights()
    return "|".join(
        [
            params.symbol,
            params.h0_source,
            f"{params.epsilon_threshold:.6f}",
            f"{params.regime_spike_sigma:.4f}:{params.regime_consecutive_bars}",
            f"{w[0]:.4f}:{w[1]:.4f}:{w[2]:.4f}",
            f"{params.paper_initial_cash:.2f}",
            f"{params.backtest_position_limit_shares:.2f}",
            f"{params.trend_epsilon_weight:.4f}:{params.trend_fair_value_weight:.4f}",
            str(params.trend_gate_sells),
            f"{params.trend_gate_z:.4f}",
            f"{params.h0_weight_oil:.4f}:{params.h0_weight_climate:.4f}",
        ]
    )


def params_draft_pending(applied: UiParams, draft: UiParams) -> bool:
    """True when sidebar draft differs from last-applied settings."""
    return backtest_params_fingerprint(applied) != backtest_params_fingerprint(draft)


def backtest_data_revision(symbol: str, *, settings: Settings | None = None) -> str:
    """Fingerprint of ingested price bars — invalidates cached UI after ingest/refresh."""
    settings = settings or Settings.from_env()
    bars = load_price_bars(symbol, MARKET_ADJ_CLOSE, settings=settings.for_symbol(symbol))
    if bars.empty:
        return "no-data"
    last = bars.index[-1]
    first = bars.index[0]
    return (
        f"{len(bars)}|{first}|{last}|"
        f"{float(bars['price'].iloc[0]):.6f}|{float(bars['price'].iloc[-1]):.6f}"
    )


def backtest_cache_key(params: UiParams) -> str:
    return f"{backtest_params_fingerprint(params)}::{backtest_data_revision(params.symbol, settings=params.to_settings())}"


DEFAULT_REFRESH_DAYS = int(os.getenv("REFRESH_DAYS", "14"))


@dataclass
class UiParams:
    symbol: str
    epsilon_threshold: float
    regime_spike_sigma: float
    regime_consecutive_bars: int
    w_return: float
    w_volume: float
    w_rel_strength: float
    paper_initial_cash: float
    paper_trade_slice_pct: float
    paper_fee_bps: float
    paper_position_limit_shares: float
    backtest_position_limit_shares: float
    h0_weight_oil: float
    h0_weight_climate: float
    trend_epsilon_weight: float
    trend_fair_value_weight: float
    trend_gate_sells: bool
    trend_gate_z: float
    h0_source: str
    epsilon_chart_window: str

    def to_settings(self) -> Settings:
        base = Settings.from_env().for_symbol(self.symbol)
        return replace(
            base,
            epsilon_threshold=self.epsilon_threshold,
            regime_spike_sigma=self.regime_spike_sigma,
            regime_consecutive_bars=self.regime_consecutive_bars,
            w_return=self.w_return,
            w_volume=self.w_volume,
            w_rel_strength=self.w_rel_strength,
            h0_weight_oil=self.h0_weight_oil,
            h0_weight_climate=self.h0_weight_climate,
            trend_epsilon_weight=self.trend_epsilon_weight,
            trend_fair_value_weight=self.trend_fair_value_weight,
            trend_gate_sells=self.trend_gate_sells,
            trend_gate_z=self.trend_gate_z,
        )

    def to_paper_settings(self) -> PaperSettings:
        base = PaperSettings.from_env()
        return PaperSettings(
            initial_cash=self.paper_initial_cash,
            position_limit_shares=self.paper_position_limit_shares,
            fee_bps=self.paper_fee_bps,
            trade_slice_pct=self.paper_trade_slice_pct,
            csv_path=base.csv_path,
        )

    def perturbation_weights(self) -> tuple[float, float, float]:
        return (self.w_return, self.w_volume, self.w_rel_strength)


def settings_for_symbol(params: UiParams, symbol: str) -> Settings:
    """Per-symbol config.json class settings + global sidebar macro/trend overrides."""
    sym = Settings.from_env().for_symbol(symbol)
    return replace(
        sym,
        h0_weight_oil=params.h0_weight_oil,
        h0_weight_climate=params.h0_weight_climate,
        trend_epsilon_weight=params.trend_epsilon_weight,
        trend_fair_value_weight=params.trend_fair_value_weight,
        trend_gate_sells=params.trend_gate_sells,
        trend_gate_z=params.trend_gate_z,
    )


def _signal_action(sig: int) -> str:
    if sig > 0:
        return "BUY"
    if sig < 0:
        return "SELL"
    return "HOLD"


def _recommendation_momentum_signal(
    *,
    rsi: float,
    momentum: float,
    position_shares: float,
    config,
) -> int:
    """Momentum signal for Recommendations — buys ignore holdings / position cap.

    Paper ``position_limit_shares`` and slice ``already long`` gating apply to
    execution (paper/backtest), not to recommendation intent. Sells still require
    a long position so we do not suggest exits while flat.
    """
    entry = momentum_backtest_signal(
        rsi=rsi,
        momentum=momentum,
        current_position=0.0,
        config=config,
    )
    if entry > 0:
        return 1
    if position_shares > 0:
        exit_sig = momentum_backtest_signal(
            rsi=rsi,
            momentum=momentum,
            current_position=position_shares,
            config=config,
        )
        if exit_sig < 0:
            return -1
    return 0


def _recommendation_note(
    *,
    epsilon: float,
    threshold: float,
    regime_valid: bool,
    signal: int,
    position_shares: float,
    z_trend: float,
    trend_gate_sells: bool,
    trend_gate_z: float,
) -> str:
    if signal > 0:
        return "Mean-reversion buy"
    if signal < 0:
        return "Exit long"
    if abs(epsilon) <= threshold:
        return "Within ε band"
    if epsilon < -threshold and not regime_valid:
        return "Buy blocked (regime)"
    if epsilon > threshold and position_shares <= 0:
        return "Overbought, flat (long-only)"
    if (
        epsilon > threshold
        and position_shares > 0
        and trend_gate_sells
        and z_trend > trend_gate_z
    ):
        return "Sell gated (uptrend)"
    return "No action"


@dataclass(frozen=True)
class RecommendationScope:
    symbols: list[str] | None
    held_symbols: frozenset[str]
    portfolio_weights: dict[str, float]
    portfolio_shares: dict[str, float]
    portfolio_values: dict[str, float]
    portfolio_name: str | None
    portfolio_file: str | None


def _portfolio_position_maps(portfolio) -> tuple[dict[str, float], dict[str, float]]:
    shares: dict[str, float] = {}
    for h in portfolio.holdings:
        if h.shares is not None:
            shares[h.symbol] = float(h.shares)
    values = portfolio_holding_values(portfolio, convert=convert_currency_value)
    return shares, values


def resolve_recommendation_scope(
    portfolio_path: Path | str | None = None,
    *,
    watchlist: list[str] | None = None,
) -> RecommendationScope:
    """Resolve portfolio holdings metadata; recommendations use the full watchlist."""
    empty = RecommendationScope(None, frozenset(), {}, {}, {}, None, None)
    if portfolio_path is None:
        return empty

    portfolio = load_portfolio_config(portfolio_path)
    if portfolio is None:
        return RecommendationScope(None, frozenset(), {}, {}, {}, None, Path(portfolio_path).name)

    portfolio_symbols = list(portfolio.symbols())
    held = frozenset(portfolio_symbols)
    weights = portfolio_weight_pcts(portfolio, convert=convert_currency_value)
    portfolio_shares, portfolio_values = _portfolio_position_maps(portfolio)
    source = portfolio.source_path.name if portfolio.source_path else Path(portfolio_path).name
    return RecommendationScope(
        None, held, weights, portfolio_shares, portfolio_values, portfolio.name, source,
    )


def fetch_recommendations(
    params: UiParams,
    *,
    assume_holding_all: bool = False,
    model: str = MODEL_PERTURBATION,
    portfolio_path: Path | str | None = None,
) -> pd.DataFrame:
    """Latest model hints for watchlist or portfolio holdings (Nordnet manual trading)."""
    base = Settings.from_env()
    scope = resolve_recommendation_scope(
        portfolio_path,
        watchlist=base.watchlist,
    )
    kwargs = {
        "assume_holding_all": assume_holding_all,
        "symbols": scope.symbols,
        "held_symbols": scope.held_symbols,
        "portfolio_weights": scope.portfolio_weights,
        "portfolio_shares": scope.portfolio_shares,
        "portfolio_values": scope.portfolio_values,
        "portfolio_name": scope.portfolio_name,
        "portfolio_file": scope.portfolio_file,
    }
    if model == MODEL_MOMENTUM_BENCHMARK:
        return _fetch_momentum_recommendations(params, **kwargs)
    if model == MODEL_AUTO:
        return _fetch_auto_recommendations(params, **kwargs)
    return _fetch_perturbation_recommendations(params, **kwargs)


def _recommendation_position_qty(
    *,
    symbol: str,
    paper_qty: float,
    assume_holding_all: bool,
    held_symbols: frozenset[str],
    assumed_eur: float,
    price: float,
    portfolio_shares: float | None = None,
    portfolio_value: float | None = None,
) -> tuple[float, bool]:
    in_portfolio = symbol in held_symbols
    assume_held = assume_holding_all or in_portfolio
    if not assume_held:
        return 0.0, False

    # Real portfolio file overrides paper-wallet dust for scoped holdings.
    if in_portfolio:
        if portfolio_shares is not None and portfolio_shares > 0:
            return portfolio_shares, False
        if portfolio_value is not None and portfolio_value > 0 and price > 0:
            return portfolio_value / price, False

    if paper_qty > 0:
        return paper_qty, False

    assumed_qty = assumed_eur / price if price > 0 else assumed_eur / 100.0
    return assumed_qty, True


def format_position_shares(qty: float, *, assumed: bool = False) -> str:
    """Human-readable units; avoid rounding small mutual-fund lots to zero."""
    if qty >= 100:
        text = f"{qty:.0f}"
    elif qty >= 1:
        text = f"{qty:.1f}"
    elif qty > 0:
        text = f"{qty:.2f}"
    else:
        text = "0"
    return f"{text}*" if assumed else text


def _sort_recommendations_by_position(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "position_shares" not in df.columns:
        return df
    sort_cols: list[str] = []
    ascending: list[bool] = []
    if "in_portfolio" in df.columns:
        sort_cols.append("in_portfolio")
        ascending.append(False)
    sort_cols.extend(["position_shares", "symbol"])
    ascending.extend([False, True])
    return df.sort_values(
        sort_cols,
        ascending=ascending,
        na_position="last",
    ).reset_index(drop=True)


def _attach_recommendation_scope_attrs(
    df: pd.DataFrame,
    *,
    assume_holding_all: bool,
    held_symbols: frozenset[str],
    portfolio_weights: dict[str, float],
    portfolio_name: str | None,
    portfolio_file: str | None,
) -> pd.DataFrame:
    df.attrs["assume_holding_all"] = assume_holding_all or bool(held_symbols)
    df.attrs["portfolio_name"] = portfolio_name
    df.attrs["portfolio_file"] = portfolio_file
    if portfolio_weights:
        df.attrs["portfolio_weights"] = portfolio_weights
    return df


def _fetch_perturbation_recommendations(
    params: UiParams,
    *,
    assume_holding_all: bool = False,
    symbols: list[str] | None = None,
    held_symbols: frozenset[str] | None = None,
    portfolio_weights: dict[str, float] | None = None,
    portfolio_shares: dict[str, float] | None = None,
    portfolio_values: dict[str, float] | None = None,
    portfolio_name: str | None = None,
    portfolio_file: str | None = None,
) -> pd.DataFrame:
    """Apply BUY/SELL rules to latest persisted ε rows (no full-series recompute)."""
    base = Settings.from_env()
    watchlist = base.watchlist
    held = held_symbols or frozenset()
    weights = portfolio_weights or {}
    shares_map = portfolio_shares or {}
    values_map = portfolio_values or {}
    if symbols is not None:
        symbols_list = list(symbols)
    else:
        symbols_list = list(watchlist)
    if not symbols_list:
        return pd.DataFrame()

    snapshots = load_latest_perturbation_snapshots(symbols_list, settings=base)
    sym_settings_by_symbol = {s: settings_for_symbol(params, s) for s in symbols_list}
    positions = get_position_quantities(settings=base)
    assumed_eur = params.to_paper_settings().slice_notional_eur()

    rows: list[dict] = []
    errors: list[str] = []
    latest_detect: pd.Timestamp | None = None
    for symbol in symbols_list:
        sym_settings = sym_settings_by_symbol[symbol]
        p = snapshots.get(symbol)
        paper_qty = positions.get(symbol, 0.0)
        if p is None:
            errors.append(symbol)
            pos_qty, pos_assumed = _recommendation_position_qty(
                symbol=symbol,
                paper_qty=paper_qty,
                assume_holding_all=assume_holding_all,
                held_symbols=held,
                assumed_eur=assumed_eur,
                price=100.0,
                portfolio_shares=shares_map.get(symbol),
                portfolio_value=values_map.get(symbol),
            )
            rows.append(
                {
                    "symbol": symbol,
                    "asset_class": sym_settings.asset_class or "etf",
                    "in_portfolio": symbol in held,
                    "portfolio_weight_pct": weights.get(symbol),
                    "as_of": None,
                    "price": None,
                    "epsilon": None,
                    "threshold": sym_settings.epsilon_threshold,
                    "regime_valid": None,
                    "z_trend": None,
                    "position_shares": pos_qty,
                    "position_assumed": pos_assumed,
                    "signal": None,
                    "action": "—",
                    "note": "No ε data — run sidebar **Run refresh** or `make detect`",
                }
            )
            continue

        if p.computed_at is not None:
            latest_detect = p.computed_at if latest_detect is None else max(latest_detect, p.computed_at)

        price = float(p.price or 0.0)
        pos_qty, pos_assumed = _recommendation_position_qty(
            symbol=symbol,
            paper_qty=paper_qty,
            assume_holding_all=assume_holding_all,
            held_symbols=held,
            assumed_eur=assumed_eur,
            price=price,
            portfolio_shares=shares_map.get(symbol),
            portfolio_value=values_map.get(symbol),
        )
        z_trend = float(p.z_trend) if p.z_trend is not None else 0.0
        threshold = sym_settings.epsilon_threshold
        sig = signal_from_epsilon(
            p.epsilon,
            threshold,
            p.regime_valid,
            long_only=True,
            current_position=pos_qty,
            **trend_signal_kwargs(sym_settings, z_trend),
        )
        rows.append(
            {
                "symbol": symbol,
                "asset_class": p.asset_class,
                "in_portfolio": symbol in held,
                "portfolio_weight_pct": weights.get(symbol),
                "as_of": p.time.strftime("%Y-%m-%d") if hasattr(p.time, "strftime") else str(p.time),
                "price": price,
                "epsilon": round(p.epsilon, 3),
                "threshold": threshold,
                "regime_valid": p.regime_valid,
                "z_trend": round(z_trend, 2) if sym_settings.trend_enable else None,
                "market_regime": p.market_regime,
                "selected_model": p.selected_model,
                "position_shares": pos_qty,
                "position_assumed": pos_assumed,
                "signal": sig,
                "action": _signal_action(sig),
                "note": _recommendation_note(
                    epsilon=p.epsilon,
                    threshold=threshold,
                    regime_valid=p.regime_valid,
                    signal=sig,
                    position_shares=pos_qty,
                    z_trend=z_trend,
                    trend_gate_sells=sym_settings.trend_gate_sells and sym_settings.trend_enable,
                    trend_gate_z=sym_settings.trend_gate_z,
                ),
            }
        )

    df = pd.DataFrame(rows)
    df = _sort_recommendations_by_position(df)
    if errors and not df.empty:
        df.attrs["errors"] = errors
    _attach_recommendation_scope_attrs(
        df,
        assume_holding_all=assume_holding_all,
        held_symbols=held,
        portfolio_weights=weights,
        portfolio_name=portfolio_name,
        portfolio_file=portfolio_file,
    )
    df.attrs["model"] = MODEL_PERTURBATION
    if latest_detect is not None:
        df.attrs["detected_at"] = str(latest_detect)[:19]
    return df


def _fetch_momentum_recommendations(
    params: UiParams,
    *,
    assume_holding_all: bool = False,
    symbols: list[str] | None = None,
    held_symbols: frozenset[str] | None = None,
    portfolio_weights: dict[str, float] | None = None,
    portfolio_shares: dict[str, float] | None = None,
    portfolio_values: dict[str, float] | None = None,
    portfolio_name: str | None = None,
    portfolio_file: str | None = None,
) -> pd.DataFrame:
    """MA crossover + momentum benchmark recommendations."""
    base = Settings.from_env()
    held = held_symbols or frozenset()
    weights = portfolio_weights or {}
    shares_map = portfolio_shares or {}
    values_map = portfolio_values or {}
    if symbols is not None:
        symbols_list = list(symbols)
    else:
        symbols_list = list(base.watchlist)
    if not symbols_list or base.universe is None:
        return pd.DataFrame()

    config = base.universe.momentum_benchmark
    snapshots = detect_latest_momentum(symbols=symbols_list, settings=base)
    by_symbol = {p.symbol: p for p in snapshots}

    positions = get_position_quantities(settings=base)
    assumed_eur = params.to_paper_settings().slice_notional_eur()

    rows: list[dict] = []
    errors: list[str] = []
    for symbol in symbols_list:
        sym_settings = settings_for_symbol(params, symbol)
        p = by_symbol.get(symbol)
        paper_qty = positions.get(symbol, 0.0)
        if p is None:
            errors.append(symbol)
            pos_qty, pos_assumed = _recommendation_position_qty(
                symbol=symbol,
                paper_qty=paper_qty,
                assume_holding_all=assume_holding_all,
                held_symbols=held,
                assumed_eur=assumed_eur,
                price=100.0,
                portfolio_shares=shares_map.get(symbol),
                portfolio_value=values_map.get(symbol),
            )
            rows.append(
                {
                    "symbol": symbol,
                    "asset_class": sym_settings.asset_class or "etf",
                    "in_portfolio": symbol in held,
                    "portfolio_weight_pct": weights.get(symbol),
                    "as_of": None,
                    "price": None,
                    "rsi": None,
                    "fast_ma": None,
                    "slow_ma": None,
                    "momentum_pct": None,
                    "rsi_bullish": None,
                    "ma_bullish": None,
                    "position_shares": pos_qty,
                    "position_assumed": pos_assumed,
                    "signal": None,
                    "action": "—",
                    "note": "No data (ingest required)",
                }
            )
            continue

        price = float(p.price)
        pos_qty, pos_assumed = _recommendation_position_qty(
            symbol=symbol,
            paper_qty=paper_qty,
            assume_holding_all=assume_holding_all,
            held_symbols=held,
            assumed_eur=assumed_eur,
            price=price,
            portfolio_shares=shares_map.get(symbol),
            portfolio_value=values_map.get(symbol),
        )
        sig = _recommendation_momentum_signal(
            rsi=p.rsi,
            momentum=p.momentum,
            position_shares=pos_qty,
            config=config,
        )
        rows.append(
            {
                "symbol": symbol,
                "asset_class": p.asset_class,
                "in_portfolio": symbol in held,
                "portfolio_weight_pct": weights.get(symbol),
                "as_of": p.time.strftime("%Y-%m-%d") if hasattr(p.time, "strftime") else str(p.time),
                "price": price,
                "rsi": round(p.rsi, 1),
                "fast_ma": round(p.fast_ma, 2) if not pd.isna(p.fast_ma) else None,
                "slow_ma": round(p.slow_ma, 2) if not pd.isna(p.slow_ma) else None,
                "momentum_pct": round(p.momentum * 100, 1) if not pd.isna(p.momentum) else None,
                "rsi_bullish": p.rsi_bullish,
                "ma_bullish": p.ma_bullish,
                "position_shares": pos_qty,
                "position_assumed": pos_assumed,
                "signal": sig,
                "action": _signal_action(sig),
                "note": _momentum_recommendation_note(
                    signal=sig,
                    price=price,
                    rsi=p.rsi,
                    rsi_bullish=p.rsi_bullish,
                    momentum=p.momentum,
                    config=config,
                    position_shares=pos_qty,
                ),
            }
        )

    df = pd.DataFrame(rows)
    df = _sort_recommendations_by_position(df)
    if errors and not df.empty:
        df.attrs["errors"] = errors
    _attach_recommendation_scope_attrs(
        df,
        assume_holding_all=assume_holding_all,
        held_symbols=held,
        portfolio_weights=weights,
        portfolio_name=portfolio_name,
        portfolio_file=portfolio_file,
    )
    df.attrs["model"] = MODEL_MOMENTUM_BENCHMARK
    return df


def _regime_note(market_regime: str, selected_model: str) -> str:
    if selected_model == MODEL_MOMENTUM_BENCHMARK:
        return f"Regime **{market_regime}** → momentum (trend-following)"
    return f"Regime **{market_regime}** → perturbation (mean-reversion)"


def _fetch_auto_recommendations(
    params: UiParams,
    *,
    assume_holding_all: bool = False,
    symbols: list[str] | None = None,
    held_symbols: frozenset[str] | None = None,
    portfolio_weights: dict[str, float] | None = None,
    portfolio_shares: dict[str, float] | None = None,
    portfolio_values: dict[str, float] | None = None,
    portfolio_name: str | None = None,
    portfolio_file: str | None = None,
) -> pd.DataFrame:
    """Route each symbol to perturbation or momentum by latest market regime."""
    scope_kwargs = {
        "assume_holding_all": assume_holding_all,
        "symbols": symbols,
        "held_symbols": held_symbols,
        "portfolio_weights": portfolio_weights,
        "portfolio_shares": portfolio_shares,
        "portfolio_values": portfolio_values,
        "portfolio_name": portfolio_name,
        "portfolio_file": portfolio_file,
    }
    pert_df = _fetch_perturbation_recommendations(params, **scope_kwargs)
    mom_df = _fetch_momentum_recommendations(params, **scope_kwargs)
    if pert_df.empty:
        return pert_df

    pert_by = {str(r["symbol"]): r for _, r in pert_df.iterrows()}
    mom_by = {str(r["symbol"]): r for _, r in mom_df.iterrows()} if not mom_df.empty else {}
    base = Settings.from_env()
    config = base.universe.momentum_benchmark if base.universe else None

    rows: list[dict] = []
    for symbol in pert_df["symbol"]:
        p = pert_by[symbol]
        m = mom_by.get(symbol)
        sym_settings = settings_for_symbol(params, symbol)

        market_regime = p.get("market_regime")
        selected = p.get("selected_model")
        if not market_regime or not selected:
            market_regime, selected = classify_latest_regime(symbol, settings=sym_settings)

        pos_qty = float(p.get("position_shares") or 0.0)
        price = p.get("price")
        threshold = p.get("threshold")

        if selected == MODEL_MOMENTUM_BENCHMARK and m is not None and m.get("signal") is not None:
            sig = int(m["signal"])
            mom_val = float("nan")
            if m.get("momentum_pct") is not None:
                mom_val = float(m["momentum_pct"]) / 100.0
            note = _momentum_recommendation_note(
                signal=sig,
                price=float(m.get("price") or 0.0),
                rsi=float(m.get("rsi") if m.get("rsi") is not None else float("nan")),
                rsi_bullish=bool(m.get("rsi_bullish")),
                momentum=mom_val,
                config=config,
                position_shares=pos_qty,
            )
            alt_note = ""
            if p.get("signal") is not None and int(p["signal"]) != sig:
                alt_note = f" (perturbation would: {_signal_action(int(p['signal']))})"
            rows.append(
                {
                    **p,
                    "market_regime": market_regime,
                    "selected_model": MODEL_MOMENTUM_BENCHMARK,
                    "signal": sig,
                    "action": _signal_action(sig),
                    "rsi": m.get("rsi"),
                    "fast_ma": m.get("fast_ma"),
                    "slow_ma": m.get("slow_ma"),
                    "momentum_pct": m.get("momentum_pct"),
                    "rsi_bullish": m.get("rsi_bullish"),
                    "ma_bullish": m.get("ma_bullish"),
                    "note": _regime_note(str(market_regime), MODEL_MOMENTUM_BENCHMARK) + " — " + note + alt_note,
                }
            )
        else:
            sig = int(p["signal"]) if p.get("signal") is not None else 0
            note = p.get("note") or ""
            alt_note = ""
            if m is not None and m.get("signal") is not None and int(m["signal"]) != sig:
                alt_note = f" (momentum would: {_signal_action(int(m['signal']))})"
            rows.append(
                {
                    **p,
                    "market_regime": market_regime,
                    "selected_model": MODEL_PERTURBATION,
                    "signal": sig,
                    "action": _signal_action(sig),
                    "note": _regime_note(str(market_regime), MODEL_PERTURBATION) + " — " + str(note) + alt_note,
                }
            )

    df = pd.DataFrame(rows)
    df = _sort_recommendations_by_position(df)
    if pert_df.attrs.get("errors"):
        df.attrs["errors"] = pert_df.attrs["errors"]
    _attach_recommendation_scope_attrs(
        df,
        assume_holding_all=assume_holding_all,
        held_symbols=held_symbols or frozenset(),
        portfolio_weights=portfolio_weights or {},
        portfolio_name=portfolio_name,
        portfolio_file=portfolio_file,
    )
    df.attrs["model"] = MODEL_AUTO
    if pert_df.attrs.get("detected_at"):
        df.attrs["detected_at"] = pert_df.attrs["detected_at"]
    return df


def _momentum_recommendation_note(
    *,
    signal: int,
    price: float,
    rsi: float,
    rsi_bullish: bool,
    momentum: float,
    config,
    position_shares: float,
) -> str:
    rsi_txt = f"{rsi:.1f}" if not pd.isna(rsi) else "n/a"
    mom_pct = f"{momentum * 100:.1f}%" if not pd.isna(momentum) else "n/a"

    if config.rsi_mode == "mean_reversion":
        if signal > 0:
            base = f"RSI {rsi_txt} < {config.rsi_oversold:.0f} — oversold buy (price {price:.2f})"
            if config.position_mode == "scale" and position_shares > 0:
                return f"{base} — add slice"
            if config.position_mode == "scale":
                return f"{base} — scale in"
            return base
        if signal < 0:
            return f"RSI {rsi_txt} > {config.rsi_overbought:.0f} — overbought exit"
        if not pd.isna(rsi) and rsi < config.rsi_oversold and position_shares > 0:
            return f"RSI {rsi_txt} oversold; hold"
        if not pd.isna(rsi) and rsi > config.rsi_overbought and position_shares <= 0:
            return f"RSI {rsi_txt} > {config.rsi_overbought:.0f}; flat (long-only)"
        if not pd.isna(rsi) and rsi >= config.rsi_oversold and position_shares <= 0:
            return f"RSI {rsi_txt} not oversold (< {config.rsi_oversold:.0f}); flat"
        return "Hold"

    if signal > 0:
        base = f"RSI {rsi_txt} ≥ {config.rsi_buy_min:.0f} (price {price:.2f})"
        if config.require_momentum_for_buy:
            base += f"; {config.momentum_lookback_days}d return {mom_pct}"
        if config.position_mode == "scale" and position_shares > 0:
            return f"{base} — add slice"
        if config.position_mode == "scale":
            return f"{base} — scale in"
        return base
    if signal < 0:
        return f"RSI {rsi_txt} < {config.rsi_sell_max:.0f} — exit long"
    if rsi_bullish and config.require_momentum_for_buy:
        if pd.isna(momentum) or momentum < config.momentum_threshold:
            return f"RSI {rsi_txt} bullish, but {config.momentum_lookback_days}d return {mom_pct} below threshold"
        return f"RSI {rsi_txt} bullish; hold"
    if not rsi_bullish and position_shares <= 0:
        return f"RSI {rsi_txt} < {config.rsi_buy_min:.0f}; flat"
    return "Hold"


def _backtest_position_limit_default() -> float:
    return float(
        os.getenv(
            "BACKTEST_POSITION_LIMIT_SHARES",
            os.getenv("PAPER_POSITION_LIMIT_SHARES", "1000"),
        )
    )


def default_ui_params(symbol: str = "VWCE.DE") -> UiParams:
    base = Settings.from_env()
    sym = base.for_symbol(symbol)
    p = PaperSettings.from_env()
    return UiParams(
        symbol=symbol,
        epsilon_threshold=sym.epsilon_threshold,
        regime_spike_sigma=sym.regime_spike_sigma,
        regime_consecutive_bars=sym.regime_consecutive_bars,
        w_return=sym.w_return,
        w_volume=sym.w_volume,
        w_rel_strength=sym.w_rel_strength,
        paper_initial_cash=p.initial_cash,
        paper_trade_slice_pct=p.trade_slice_pct,
        paper_fee_bps=p.fee_bps,
        paper_position_limit_shares=p.position_limit_shares,
        backtest_position_limit_shares=_backtest_position_limit_default(),
        h0_weight_oil=base.h0_weight_oil,
        h0_weight_climate=base.h0_weight_climate,
        trend_epsilon_weight=sym.trend_epsilon_weight,
        trend_fair_value_weight=sym.trend_fair_value_weight,
        trend_gate_sells=sym.trend_gate_sells,
        trend_gate_z=sym.trend_gate_z,
        h0_source=H0_SOURCE_SAVED,
        epsilon_chart_window=CHART_WINDOW_RECENT,
    )


def equilibrium_status(symbol: str, *, settings: Settings | None = None) -> dict | None:
    params = load_latest_equilibrium_params(symbol, settings=settings)
    if params is None:
        return None
    return {
        "source": "saved",
        "kappa": params["kappa"],
        "mu": params["mu"],
        "sigma": params["sigma"],
        "half_life_days": params["half_life_days"],
        "calibrated_at": str(params["calibrated_at"]),
    }


def active_equilibrium_status(
    symbol: str,
    *,
    h0_source: str,
    settings: Settings | None = None,
) -> dict | None:
    """H₀ params that will be used for ε given the selected source."""
    settings = settings or Settings.from_env()
    bars = load_price_bars(symbol, MARKET_ADJ_CLOSE, settings=settings)
    if bars.empty:
        return None
    try:
        model = resolve_h0_equilibrium(symbol, h0_source=h0_source, all_data=bars, settings=settings)
    except ValueError:
        return None
    status = {
        "source": "saved (DB)" if h0_source == H0_SOURCE_SAVED else "walk-forward (train 70%)",
        "kappa": model.kappa,
        "mu": model.mu,
        "sigma": model.sigma,
        "half_life_days": model.half_life_days,
    }
    if h0_source == H0_SOURCE_SAVED:
        saved = load_latest_equilibrium_params(symbol, settings=settings)
        if saved:
            status["calibrated_at"] = str(saved["calibrated_at"])
    else:
        train_end, _ = backtest_train_test_split(bars.index)
        status["train_end"] = str(train_end)
    return status


def backtest_test_start(symbol: str, *, settings: Settings | None = None) -> pd.Timestamp | None:
    settings = settings or Settings.from_env()
    bars = load_price_bars(symbol, MARKET_ADJ_CLOSE, settings=settings)
    if bars.empty:
        return None
    _, test_start = backtest_train_test_split(bars.index)
    return test_start


def _slice_series_for_chart_window(
    series: pd.DataFrame,
    *,
    symbol: str,
    window: str,
    settings: Settings | None = None,
    recent_bars: int = 120,
) -> pd.DataFrame:
    if series.empty:
        return series
    if window == CHART_WINDOW_BACKTEST_TEST:
        test_start = backtest_test_start(symbol, settings=settings)
        if test_start is not None:
            sliced = series[series.index >= test_start]
            if not sliced.empty:
                return sliced
    return series.tail(recent_bars)


def slice_perturbation_for_chart(
    series: pd.DataFrame,
    *,
    symbol: str,
    window: str,
    settings: Settings | None = None,
) -> pd.DataFrame:
    """Filter ε series for chart display (Trade tab vs backtest test window)."""
    return _slice_series_for_chart_window(series, symbol=symbol, window=window, settings=settings)


def slice_momentum_for_chart(
    series: pd.DataFrame,
    *,
    symbol: str,
    window: str,
    settings: Settings | None = None,
) -> pd.DataFrame:
    """Filter MA/momentum series for Trade tab chart window."""
    return _slice_series_for_chart_window(series, symbol=symbol, window=window, settings=settings)


def perturbation_context(
    symbol: str,
    *,
    weights: tuple[float, float, float] | None = None,
    settings: Settings | None = None,
    h0_source: str = H0_SOURCE_SAVED,
) -> pd.DataFrame:
    if settings is None:
        sym_settings = Settings.from_env().for_symbol(symbol)
    else:
        sym_settings = settings
    equilibrium = None
    if h0_source == H0_SOURCE_WALK_FORWARD:
        bars = load_price_bars(symbol, MARKET_ADJ_CLOSE, settings=sym_settings)
        if not bars.empty:
            equilibrium = resolve_h0_equilibrium(
                symbol, h0_source=h0_source, all_data=bars, settings=sym_settings,
            )
    if weights is None:
        weights = sym_settings.perturbation_weights()
    return compute_perturbation_series(
        symbol, weights=weights, settings=sym_settings, equilibrium=equilibrium,
    )


def momentum_trade_context(
    symbol: str,
    *,
    settings: Settings | None = None,
    window: str = CHART_WINDOW_RECENT,
    current_position: float = 0.0,
) -> dict:
    """Latest momentum benchmark features for the Trade tab."""
    settings = settings or Settings.from_env().for_symbol(symbol)
    if settings.universe is None:
        return {"error": "Momentum benchmark requires config.json universe", "price_overlay": pd.DataFrame()}

    config = settings.universe.momentum_benchmark
    series = compute_momentum_series(symbol, settings=settings, config=config)
    if series.empty:
        return {"error": "No price data for momentum series", "price_overlay": pd.DataFrame()}

    chart_series = slice_momentum_for_chart(
        series, symbol=symbol, window=window, settings=settings,
    )
    latest = chart_series.iloc[-1]
    if pd.isna(latest.get("rsi")):
        return {"error": "RSI not ready yet", "price_overlay": pd.DataFrame()}

    momentum_val = float(latest["momentum"]) if not pd.isna(latest["momentum"]) else float("nan")
    rsi_val = float(latest["rsi"])
    signal = momentum_backtest_signal(
        rsi=rsi_val,
        momentum=momentum_val,
        current_position=current_position,
        config=config,
    )
    price_overlay = build_momentum_price_overlay(
        chart_series, slow_ma_days=config.slow_ma_days,
    )
    rsi_chart = build_rsi_chart_frame(
        chart_series,
        rsi_mode=config.rsi_mode,
        rsi_buy_min=config.rsi_buy_min,
        rsi_sell_max=config.rsi_sell_max,
        rsi_oversold=config.rsi_oversold,
        rsi_overbought=config.rsi_overbought,
    )

    return {
        "error": None,
        "fast_ma_days": config.fast_ma_days,
        "slow_ma_days": config.slow_ma_days,
        "rsi_period": config.rsi_period,
        "rsi_mode": config.rsi_mode,
        "rsi_buy_min": config.rsi_buy_min,
        "rsi_sell_max": config.rsi_sell_max,
        "rsi_oversold": config.rsi_oversold,
        "rsi_overbought": config.rsi_overbought,
        "momentum_lookback_days": config.momentum_lookback_days,
        "fast_ma": float(latest["fast_ma"]) if not pd.isna(latest.get("fast_ma")) else None,
        "slow_ma": float(latest["slow_ma"]) if not pd.isna(latest.get("slow_ma")) else None,
        "rsi": rsi_val,
        "price": float(latest["price"]),
        "momentum": None if pd.isna(momentum_val) else momentum_val,
        "momentum_pct": None if pd.isna(momentum_val) else momentum_val * 100.0,
        "rsi_bullish": bool(latest["rsi_bullish"]),
        "rsi_oversold": bool(latest["rsi_oversold"]) if not pd.isna(latest.get("rsi_oversold")) else False,
        "rsi_overbought": bool(latest["rsi_overbought"]) if not pd.isna(latest.get("rsi_overbought")) else False,
        "ma_bullish": bool(latest["ma_bullish"]) if not pd.isna(latest.get("ma_bullish")) else False,
        "signal": signal,
        "action": _signal_action(signal),
        "as_of": chart_series.index[-1],
        "price_overlay": price_overlay,
        "rsi_chart": rsi_chart,
    }


def fetch_portfolio_allocation(
    portfolio_path: Path | str | None = None,
) -> PortfolioAllocationResult | None:
    """Look-through allocation from portfolio JSON + fund_profiles/."""
    portfolio = load_portfolio_config(portfolio_path)
    return compute_portfolio_allocation(portfolio)


def fetch_portfolio_performance(
    holdings: pd.DataFrame,
    *,
    base_date,
    currency: str,
    settings: Settings | None = None,
) -> PortfolioPerformanceResult:
    """Price % change and PnL vs base date for portfolio holdings."""
    return compute_portfolio_performance(
        holdings,
        base_date=base_date,
        currency=currency,
        settings=settings,
    )


def fetch_top_hints_overlay(
    params: UiParams,
    *,
    portfolio_path: Path | str | None = None,
    max_adds: int = 3,
    max_trims: int = 2,
) -> pd.DataFrame:
    """Top ε-ranked add/trim hints — always perturbation-based, independent of selected model."""
    rec = fetch_recommendations(
        params,
        assume_holding_all=False,
        model=MODEL_PERTURBATION,
        portfolio_path=portfolio_path,
    )
    return build_portfolio_overlay(rec, max_adds=max_adds, max_trims=max_trims)


def fetch_portfolio_overlay(
    params: UiParams,
    *,
    portfolio_path: Path | str | None,
    max_adds: int = 3,
    max_trims: int = 2,
    model: str = MODEL_PERTURBATION,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """ε-ranked add/trim hints + full recommendations for the selected model."""
    rec = fetch_recommendations(
        params,
        assume_holding_all=False,
        model=model,
        portfolio_path=portfolio_path,
    )
    overlay = fetch_top_hints_overlay(
        params, portfolio_path=portfolio_path, max_adds=max_adds, max_trims=max_trims,
    )
    return overlay, rec


def watchlist_with_class(settings: Settings | None = None) -> list[tuple[str, str]]:
    """(symbol, asset_class) pairs in config order."""
    settings = settings or Settings.from_env()
    if settings.universe is None:
        return [(s, "etf") for s in settings.watchlist]
    rows: list[tuple[str, str]] = []
    for name, cfg in settings.universe.by_class().items():
        for sym in cfg.symbols:
            rows.append((sym, name))
    return rows


def buy_hold_display(view: dict) -> dict:
    """Recompute passive hold from stored test-period prices (display source of truth)."""
    initial = float(view.get("initial_capital_eur", 0.0))
    price_chart = view.get("price_chart")
    if isinstance(price_chart, pd.DataFrame) and not price_chart.empty:
        prices = price_chart.set_index(pd.to_datetime(price_chart["time"]))["price"].astype(float)
        bh = buy_and_hold_from_prices(prices, initial)
        start = str(prices.index[0])[:10]
        end = str(prices.index[-1])[:10]
        return {**bh, "test_start_date": start, "test_end_date": end}
    return {
        "profit_eur": float(view.get("buy_and_hold_profit_eur", 0.0)),
        "first_price": float(view.get("buy_and_hold_first_price", 0.0)),
        "last_price": float(view.get("buy_and_hold_last_price", 0.0)),
        "return_pct": float(view.get("buy_and_hold_return_pct", 0.0)),
        "final_eur": float(view.get("buy_and_hold_final_eur", initial)),
        "test_start_date": str(view.get("test_start", ""))[:10],
        "test_end_date": str(view.get("price_as_of", ""))[:10],
    }


def suggest_epsilon_threshold(epsilon: pd.Series, *, quantile: float = 0.75) -> float:
    """Highest ε threshold that still produces buy signals (long-only mean reversion)."""
    if epsilon.empty:
        return 0.5
    neg = epsilon[epsilon < 0].abs()
    if neg.empty:
        return 0.5
    # Highest threshold with at least one buy day in the test window.
    best = 0.35
    for step in range(35, 150):
        th = step / 100.0
        if (epsilon < -th).sum() >= 1:
            best = th
    tail = float(neg.quantile(quantile))
    suggested = min(best, tail * 1.05)
    return round(max(0.35, min(0.55, suggested)), 2)


def run_backtest_for_ui(params: UiParams) -> dict:
    settings = params.to_settings()
    requested_threshold = params.epsilon_threshold
    result = run_backtest(
        params.symbol,
        epsilon_threshold=requested_threshold,
        weights=params.perturbation_weights(),
        initial_cash_eur=params.paper_initial_cash,
        position_limit_shares=params.backtest_position_limit_shares,
        settings=settings,
        persist=False,
        h0_source=params.h0_source,
    )
    eps = result.epsilon.astype(float)
    suggested = suggest_epsilon_threshold(eps)
    effective_threshold = requested_threshold
    threshold_adjusted = False
    if result.total_trades == 0 and suggested < requested_threshold - 0.01:
        result = run_backtest(
            params.symbol,
            epsilon_threshold=suggested,
            weights=params.perturbation_weights(),
            initial_cash_eur=params.paper_initial_cash,
            position_limit_shares=params.backtest_position_limit_shares,
            settings=settings,
            persist=False,
            h0_source=params.h0_source,
        )
        eps = result.epsilon.astype(float)
        effective_threshold = suggested
        threshold_adjusted = True
    m = result.metrics
    threshold = effective_threshold
    buy_signals = int((eps < -threshold).sum())
    sell_signals = int((eps > threshold).sum())
    regime = result.regime_valid.astype(bool)
    buy_with_regime = int(((eps < -threshold) & regime).sum())
    buy_blocked_regime = int(((eps < -threshold) & ~regime).sum())
    price_as_of = None
    if not result.price.empty:
        price_as_of = str(result.price.index[-1])
    data_revision = backtest_data_revision(params.symbol, settings=settings)
    h0_status = active_equilibrium_status(
        params.symbol, h0_source=params.h0_source, settings=settings,
    )
    bh = buy_and_hold_from_prices(result.price, params.paper_initial_cash)

    momentum_result = None
    momentum_error: str | None = None
    try:
        momentum_result = run_momentum_backtest(
            params.symbol,
            test_start=result.test_start,
            initial_cash_eur=params.paper_initial_cash,
            position_limit_shares=params.backtest_position_limit_shares,
            settings=settings,
        )
    except Exception as exc:
        momentum_error = str(exc)

    mixed_result = None
    mixed_error: str | None = None
    try:
        mixed_result = run_mixed_backtest(
            params.symbol,
            test_start=result.test_start,
            epsilon_threshold=effective_threshold,
            weights=params.perturbation_weights(),
            initial_cash_eur=params.paper_initial_cash,
            position_limit_shares=params.backtest_position_limit_shares,
            settings=settings,
            h0_source=params.h0_source,
        )
    except Exception as exc:
        mixed_error = str(exc)

    model_comparison_chart = pd.DataFrame()
    model_position_chart = pd.DataFrame()
    regime_timeline_chart = pd.DataFrame()
    index = result.equity_curve.index
    if momentum_result is not None:
        index = momentum_result.equity_curve.index
    pert_eq = result.equity_curve.reindex(index, method="ffill")
    pert_pos = result.position_shares.reindex(index, method="ffill").fillna(0.0)
    chart_data = {
        "time": index,
        "Perturbation": pert_eq.values,
    }
    pos_data = {
        "time": index,
        "Perturbation": pert_pos.values,
    }
    if momentum_result is not None:
        chart_data["Momentum benchmark"] = momentum_result.equity_curve.reindex(index).values
        pos_data["Momentum benchmark"] = momentum_result.position_shares.reindex(index).fillna(0.0).values
    if mixed_result is not None:
        chart_data["Mixed (auto)"] = mixed_result.equity_curve.reindex(index).values
        pos_data["Mixed (auto)"] = mixed_result.position_shares.reindex(index).fillna(0.0).values
        regime_timeline_chart = pd.DataFrame(
            {
                "time": mixed_result.market_regime.index,
                "market_regime": mixed_result.market_regime.values,
                "selected_model": mixed_result.selected_model.values,
            }
        )
    model_comparison_chart = pd.DataFrame(chart_data)
    model_position_chart = pd.DataFrame(pos_data)

    momentum_view: dict = {"error": momentum_error}
    if momentum_result is not None:
        mm = momentum_result.metrics
        mom_cfg = settings.universe.momentum_benchmark if settings.universe else None
        rsi_chart = pd.DataFrame()
        rsi_params: dict | None = None
        if mom_cfg is not None:
            rsi_src = pd.DataFrame(
                {"rsi": momentum_result.rsi.values},
                index=momentum_result.price.index,
            )
            rsi_chart = build_rsi_chart_frame(
                rsi_src,
                rsi_mode=mom_cfg.rsi_mode,
                rsi_buy_min=mom_cfg.rsi_buy_min,
                rsi_sell_max=mom_cfg.rsi_sell_max,
                rsi_oversold=mom_cfg.rsi_oversold,
                rsi_overbought=mom_cfg.rsi_overbought,
            )
            rsi_params = {
                "rsi_mode": mom_cfg.rsi_mode,
                "rsi_period": mom_cfg.rsi_period,
                "rsi_buy_min": mom_cfg.rsi_buy_min,
                "rsi_sell_max": mom_cfg.rsi_sell_max,
                "rsi_oversold": mom_cfg.rsi_oversold,
                "rsi_overbought": mom_cfg.rsi_overbought,
            }
        momentum_view = {
            "error": None,
            "net_profit_eur": mm.get("net_profit_eur", momentum_result.total_return),
            "return_pct": mm.get("return_pct", 0.0),
            "final_portfolio_eur": mm.get("final_portfolio_eur", params.paper_initial_cash),
            "sharpe": momentum_result.sharpe,
            "max_drawdown": momentum_result.max_drawdown,
            "total_trades": momentum_result.total_trades,
            "fast_ma_days": mm.get("fast_ma_days"),
            "slow_ma_days": mm.get("slow_ma_days"),
            "momentum_lookback_days": mm.get("momentum_lookback_days"),
            "rsi_period": mom_cfg.rsi_period if mom_cfg else None,
            "rsi_mode": mom_cfg.rsi_mode if mom_cfg else None,
            "equity_curve": pd.DataFrame(
                {
                    "time": momentum_result.equity_curve.index,
                    "portfolio_eur": momentum_result.equity_curve.values,
                }
            ),
            "ma_chart": pd.DataFrame(
                {
                    "time": momentum_result.price.index,
                    "price": momentum_result.price.values,
                    "Fast MA": momentum_result.fast_ma.values,
                    "Slow MA": momentum_result.slow_ma.values,
                }
            ),
            "rsi_chart": rsi_chart,
            "rsi_params": rsi_params,
        }

    return {
        "cache_key": backtest_cache_key(params),
        "fingerprint": backtest_params_fingerprint(params),
        "data_revision": data_revision,
        "h0_status": h0_status,
        "price_as_of": price_as_of,
        "symbol": params.symbol,
        "epsilon_threshold": threshold,
        "requested_threshold": requested_threshold,
        "threshold_adjusted": threshold_adjusted,
        "suggested_threshold": suggested,
        "epsilon_max_abs": float(eps.abs().max()) if not eps.empty else 0.0,
        "buy_model_signals": buy_signals,
        "sell_model_signals": sell_signals,
        "buy_signals_with_regime": buy_with_regime,
        "buy_signals_blocked_by_regime": buy_blocked_regime,
        "regime_invalid_days": int((~regime).sum()),
        "regime_spike_sigma": settings.regime_spike_sigma,
        "regime_consecutive_bars": settings.regime_consecutive_bars,
        "h0_source": result.h0_source,
        "test_start": str(result.test_start) if result.test_start is not None else None,
        "equilibrium_half_life_days": result.equilibrium_half_life_days,
        "position_limit_shares": m.get("position_limit_shares", params.backtest_position_limit_shares),
        "initial_capital_eur": m.get("initial_capital_eur", params.paper_initial_cash),
        "final_portfolio_eur": m.get("final_portfolio_eur", params.paper_initial_cash),
        "net_profit_eur": m.get("net_profit_eur", result.total_return),
        "return_pct": m.get("return_pct", 0.0),
        "final_cash_eur": m.get("final_cash_eur", params.paper_initial_cash),
        "final_shares": m.get("final_shares", 0.0),
        "avg_cost_eur": m.get("avg_cost_eur", 0.0),
        "realized_pnl_eur": m.get("realized_pnl_eur", 0.0),
        "unrealized_pnl_eur": m.get("unrealized_pnl_eur", 0.0),
        "total_pnl_eur": m.get("total_pnl_eur", 0.0),
        "total_fees_eur": m.get("total_fees_eur", 0.0),
        "buy_and_hold_profit_eur": bh["profit_eur"],
        "buy_and_hold_first_price": bh["first_price"],
        "buy_and_hold_last_price": bh["last_price"],
        "buy_and_hold_return_pct": bh["return_pct"],
        "buy_and_hold_final_eur": bh["final_eur"],
        "sharpe": result.sharpe,
        "max_drawdown": result.max_drawdown,
        "total_return": result.total_return,
        "total_trades": result.total_trades,
        "regime_invalidations": result.regime_invalidations,
        "equity_curve": pd.DataFrame(
            {"time": result.equity_curve.index, "portfolio_eur": result.equity_curve.values}
        ),
        "pnl_curve": pd.DataFrame(
            {
                "time": result.realized_pnl.index,
                "realized_pnl": result.realized_pnl.values,
                "unrealized_pnl": result.unrealized_pnl.values,
                "shares_bought": result.trade_volume_shares.where(result.trade_signal > 0, 0.0).values,
                "shares_sold": result.trade_volume_shares.where(result.trade_signal < 0, 0.0).values,
                "position_shares": result.position_shares.values,
            }
        ),
        "epsilon": pd.DataFrame({"time": eps.index, "epsilon": eps.values}),
        "trade_chart": pd.DataFrame(
            {
                "time": eps.index,
                "epsilon": eps.values,
                "regime_valid": result.regime_valid.values,
                "trade_signal": result.trade_signal.values,
            }
        ),
        "price_chart": pd.DataFrame(
            {
                "time": result.price.index,
                "price": result.price.astype(float).values,
            }
        ).copy(),
        "model_comparison_chart": model_comparison_chart,
        "model_position_chart": model_position_chart,
        "regime_timeline_chart": regime_timeline_chart,
        "mixed_benchmark": {
            "error": mixed_error,
            "sharpe": mixed_result.sharpe if mixed_result else None,
            "total_trades": mixed_result.total_trades if mixed_result else None,
            "net_profit_eur": mixed_result.metrics.get("net_profit_eur") if mixed_result else None,
            "return_pct": mixed_result.metrics.get("return_pct") if mixed_result else None,
        },
        "momentum_benchmark": momentum_view,
    }


def run_refresh(
    *,
    days: int = DEFAULT_REFRESH_DAYS,
    settings: Settings | None = None,
    asset_classes: str | list[str] | None = None,
) -> dict:
    """Same pipeline as `make refresh`: ingest → factors → detect."""
    from funtrade.universe_config import parse_asset_classes

    settings = settings or Settings.from_env()
    symbols: list[str] | None = None
    if asset_classes:
        parsed = parse_asset_classes(asset_classes)
        symbols = settings.universe.symbols_for_classes(parsed) if settings.universe else []
    out: dict = {"days": days, "steps": {}}
    if symbols is not None:
        out["asset_classes"] = list(parse_asset_classes(asset_classes))
        out["symbols"] = symbols

    try:
        ingest_counts = ingest_watchlist(days=days, symbols=symbols, settings=settings)
        out["steps"]["ingest"] = {
            "ok": True,
            "rows_upserted": ingest_counts,
            "total_rows": int(sum(ingest_counts.values())),
        }
    except Exception as exc:
        out["steps"]["ingest"] = {"ok": False, "error": str(exc)}
        out["ok"] = False
        return out

    try:
        factor_counts = ingest_macro_factors(days=days, settings=settings)
        out["steps"]["ingest_factors"] = {
            "ok": True,
            "counts": factor_counts,
            "total_rows": int(sum(factor_counts.values())),
        }
    except Exception as exc:
        out["steps"]["ingest_factors"] = {"ok": False, "error": str(exc)}
        out["ok"] = False
        return out

    try:
        detections = detect_latest_perturbations(settings=settings, symbols=symbols, persist=True)
        out["steps"]["detect"] = {
            "ok": True,
            "symbols": len(detections),
            "results": [
                {
                    "symbol": r.symbol,
                    "epsilon": round(r.epsilon, 3),
                    "regime_valid": r.regime_valid,
                }
                for r in detections
            ],
        }
        out["ok"] = True
    except Exception as exc:
        out["steps"]["detect"] = {"ok": False, "error": str(exc)}
        out["ok"] = False

    return out
