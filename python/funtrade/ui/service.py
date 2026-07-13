"""Helpers for the Streamlit trading console."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

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
from funtrade.paper.runner import run_paper_once
from funtrade.portfolio.allocation import PortfolioAllocationResult, compute_portfolio_allocation
from funtrade.ui.plotting.data import build_momentum_price_overlay

CHART_WINDOW_RECENT = "recent_120"
CHART_WINDOW_BACKTEST_TEST = "backtest_test"

MODEL_PERTURBATION = "perturbation"
MODEL_MOMENTUM_BENCHMARK = "momentum_benchmark"
MODEL_AUTO = "auto"
RECOMMENDATION_MODELS = (MODEL_PERTURBATION, MODEL_MOMENTUM_BENCHMARK, MODEL_AUTO)


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
        ]
    )


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


def fetch_recommendations(
    params: UiParams,
    *,
    assume_holding_all: bool = False,
    model: str = MODEL_PERTURBATION,
) -> pd.DataFrame:
    """Latest model hints for every symbol in config.json (Nordnet manual trading)."""
    if model == MODEL_MOMENTUM_BENCHMARK:
        return _fetch_momentum_recommendations(params, assume_holding_all=assume_holding_all)
    if model == MODEL_AUTO:
        return _fetch_auto_recommendations(params, assume_holding_all=assume_holding_all)
    return _fetch_perturbation_recommendations(params, assume_holding_all=assume_holding_all)


def _fetch_perturbation_recommendations(
    params: UiParams,
    *,
    assume_holding_all: bool = False,
) -> pd.DataFrame:
    """Apply BUY/SELL rules to latest persisted ε rows (no full-series recompute)."""
    base = Settings.from_env()
    symbols = base.watchlist
    if not symbols:
        return pd.DataFrame()

    snapshots = load_latest_perturbation_snapshots(symbols, settings=base)
    sym_settings_by_symbol = {s: settings_for_symbol(params, s) for s in symbols}
    positions = get_position_quantities(settings=base)
    assumed_eur = params.to_paper_settings().slice_notional_eur()

    rows: list[dict] = []
    errors: list[str] = []
    latest_detect: pd.Timestamp | None = None
    for symbol in symbols:
        sym_settings = sym_settings_by_symbol[symbol]
        p = snapshots.get(symbol)
        paper_qty = positions.get(symbol, 0.0)
        if p is None:
            errors.append(symbol)
            rows.append(
                {
                    "symbol": symbol,
                    "asset_class": sym_settings.asset_class or "etf",
                    "as_of": None,
                    "price": None,
                    "epsilon": None,
                    "threshold": sym_settings.epsilon_threshold,
                    "regime_valid": None,
                    "z_trend": None,
                    "position_shares": paper_qty,
                    "position_assumed": False,
                    "signal": None,
                    "action": "—",
                    "note": "No ε data — run sidebar **Run refresh** or `make detect`",
                }
            )
            continue

        if p.computed_at is not None:
            latest_detect = p.computed_at if latest_detect is None else max(latest_detect, p.computed_at)

        pos_assumed = assume_holding_all and paper_qty <= 0
        price = float(p.price or 0.0)
        assumed_qty = assumed_eur / price if price > 0 else assumed_eur / 100.0
        pos_qty = paper_qty if paper_qty > 0 else (assumed_qty if assume_holding_all else 0.0)
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
    if errors and not df.empty:
        df.attrs["errors"] = errors
    df.attrs["assume_holding_all"] = assume_holding_all
    df.attrs["model"] = MODEL_PERTURBATION
    if latest_detect is not None:
        df.attrs["detected_at"] = str(latest_detect)[:19]
    return df


def _fetch_momentum_recommendations(
    params: UiParams,
    *,
    assume_holding_all: bool = False,
) -> pd.DataFrame:
    """MA crossover + momentum benchmark recommendations."""
    base = Settings.from_env()
    symbols = base.watchlist
    if not symbols or base.universe is None:
        return pd.DataFrame()

    config = base.universe.momentum_benchmark
    snapshots = detect_latest_momentum(symbols=symbols, settings=base)
    by_symbol = {p.symbol: p for p in snapshots}

    positions = get_position_quantities(settings=base)
    assumed_eur = params.to_paper_settings().slice_notional_eur()

    rows: list[dict] = []
    errors: list[str] = []
    for symbol in symbols:
        sym_settings = settings_for_symbol(params, symbol)
        p = by_symbol.get(symbol)
        paper_qty = positions.get(symbol, 0.0)
        if p is None:
            errors.append(symbol)
            rows.append(
                {
                    "symbol": symbol,
                    "asset_class": sym_settings.asset_class or "etf",
                    "as_of": None,
                    "price": None,
                    "fast_ma": None,
                    "slow_ma": None,
                    "momentum_pct": None,
                    "ma_bullish": None,
                    "position_shares": paper_qty,
                    "position_assumed": False,
                    "signal": None,
                    "action": "—",
                    "note": "No data (ingest required)",
                }
            )
            continue

        pos_assumed = assume_holding_all and paper_qty <= 0
        price = float(p.price)
        assumed_qty = assumed_eur / price if price > 0 else assumed_eur / 100.0
        pos_qty = paper_qty if paper_qty > 0 else (assumed_qty if assume_holding_all else 0.0)
        sig = momentum_backtest_signal(
            fast_ma=p.fast_ma,
            slow_ma=p.slow_ma,
            momentum=p.momentum,
            current_position=pos_qty,
            config=config,
        )
        rows.append(
            {
                "symbol": symbol,
                "asset_class": p.asset_class,
                "as_of": p.time.strftime("%Y-%m-%d") if hasattr(p.time, "strftime") else str(p.time),
                "price": price,
                "fast_ma": round(p.fast_ma, 2),
                "slow_ma": round(p.slow_ma, 2),
                "momentum_pct": round(p.momentum * 100, 1) if not pd.isna(p.momentum) else None,
                "ma_bullish": p.ma_bullish,
                "position_shares": pos_qty,
                "position_assumed": pos_assumed,
                "signal": sig,
                "action": _signal_action(sig),
                "note": _momentum_recommendation_note(
                    signal=sig,
                    price=price,
                    fast_ma=p.fast_ma,
                    slow_ma=p.slow_ma,
                    ma_bullish=p.ma_bullish,
                    momentum=p.momentum,
                    config=config,
                    position_shares=pos_qty,
                ),
            }
        )

    df = pd.DataFrame(rows)
    if errors and not df.empty:
        df.attrs["errors"] = errors
    df.attrs["assume_holding_all"] = assume_holding_all
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
) -> pd.DataFrame:
    """Route each symbol to perturbation or momentum by latest market regime."""
    pert_df = _fetch_perturbation_recommendations(params, assume_holding_all=assume_holding_all)
    mom_df = _fetch_momentum_recommendations(params, assume_holding_all=assume_holding_all)
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
                fast_ma=float(m.get("fast_ma") or 0.0),
                slow_ma=float(m.get("slow_ma") or 0.0),
                ma_bullish=bool(m.get("ma_bullish")),
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
                    "fast_ma": m.get("fast_ma"),
                    "slow_ma": m.get("slow_ma"),
                    "momentum_pct": m.get("momentum_pct"),
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
    if pert_df.attrs.get("errors"):
        df.attrs["errors"] = pert_df.attrs["errors"]
    df.attrs["assume_holding_all"] = assume_holding_all
    df.attrs["model"] = MODEL_AUTO
    if pert_df.attrs.get("detected_at"):
        df.attrs["detected_at"] = pert_df.attrs["detected_at"]
    return df


def _momentum_recommendation_note(
    *,
    signal: int,
    price: float,
    fast_ma: float,
    slow_ma: float,
    ma_bullish: bool,
    momentum: float,
    config,
    position_shares: float,
) -> str:
    mom_pct = f"{momentum * 100:.1f}%" if not pd.isna(momentum) else "n/a"
    if signal > 0:
        base = (
            f"Fast MA ({fast_ma:.2f}) > slow MA ({slow_ma:.2f}); "
            f"63d momentum {mom_pct} (price {price:.2f})"
        )
        if config.position_mode == "scale" and position_shares > 0:
            return f"{base} — add slice"
        if config.position_mode == "scale":
            return f"{base} — scale in"
        return base
    if signal < 0:
        return f"Fast MA ({fast_ma:.2f}) < slow MA ({slow_ma:.2f}) — exit long"
    if ma_bullish and config.require_momentum_for_buy:
        if pd.isna(momentum) or momentum < config.momentum_threshold:
            return f"Fast > slow, but 63d momentum {mom_pct} below threshold"
        if config.position_mode == "slice" and position_shares > 0:
            return "Fast > slow; already long (slice mode — one entry)"
        return "Fast > slow; hold"
    if not ma_bullish and position_shares <= 0:
        return f"Fast MA ({fast_ma:.2f}) ≤ slow MA ({slow_ma:.2f}); flat"
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
    if pd.isna(latest.get("fast_ma")) or pd.isna(latest.get("slow_ma")):
        return {"error": "Moving averages not ready yet", "price_overlay": pd.DataFrame()}

    momentum_val = float(latest["momentum"]) if not pd.isna(latest["momentum"]) else float("nan")
    signal = momentum_backtest_signal(
        fast_ma=float(latest["fast_ma"]),
        slow_ma=float(latest["slow_ma"]),
        momentum=momentum_val,
        current_position=current_position,
        config=config,
    )
    price_overlay = build_momentum_price_overlay(
        chart_series, slow_ma_days=config.slow_ma_days,
    )

    return {
        "error": None,
        "fast_ma_days": config.fast_ma_days,
        "slow_ma_days": config.slow_ma_days,
        "momentum_lookback_days": config.momentum_lookback_days,
        "fast_ma": float(latest["fast_ma"]),
        "slow_ma": float(latest["slow_ma"]),
        "price": float(latest["price"]),
        "momentum": None if pd.isna(momentum_val) else momentum_val,
        "momentum_pct": None if pd.isna(momentum_val) else momentum_val * 100.0,
        "ma_bullish": bool(latest["ma_bullish"]),
        "signal": signal,
        "action": _signal_action(signal),
        "as_of": chart_series.index[-1],
        "price_overlay": price_overlay,
    }


def fetch_portfolio_allocation() -> PortfolioAllocationResult | None:
    """Look-through allocation from portfolio.json + fund_profiles/."""
    return compute_portfolio_allocation()


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
        }

    return {
        "cache_key": backtest_cache_key(params),
        "fingerprint": backtest_params_fingerprint(params),
        "data_revision": data_revision,
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
    paper: PaperSettings | None = None,
) -> dict:
    """Same pipeline as `make refresh`: ingest → factors → detect → paper."""
    settings = settings or Settings.from_env()
    paper = paper or PaperSettings.from_env()
    out: dict = {"days": days, "steps": {}}

    try:
        ingest_counts = ingest_watchlist(days=days, settings=settings)
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
        detections = detect_latest_perturbations(settings=settings, persist=True)
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
    except Exception as exc:
        out["steps"]["detect"] = {"ok": False, "error": str(exc)}
        out["ok"] = False
        return out

    try:
        paper_results = run_paper_once(settings=settings, paper=paper)
        fills = sum(1 for r in paper_results if r.get("fill") is not None)
        out["steps"]["paper"] = {
            "ok": True,
            "symbols": len(paper_results),
            "fills": fills,
            "results": paper_results,
        }
        out["ok"] = True
    except Exception as exc:
        out["steps"]["paper"] = {"ok": False, "error": str(exc)}
        out["ok"] = False

    return out
