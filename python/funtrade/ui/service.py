"""Helpers for the Streamlit trading console."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

import pandas as pd

from funtrade.backtest.engine import (
    H0_SOURCE_SAVED,
    H0_SOURCE_WALK_FORWARD,
    backtest_train_test_split,
    resolve_h0_equilibrium,
    run_backtest,
)
from funtrade.config import Settings
from funtrade.data.factors import ingest_macro_factors
from funtrade.data.ingest import ingest_watchlist
from funtrade.data.loader import MARKET_ADJ_CLOSE, load_latest_equilibrium_params, load_price_bars
from funtrade.execution.paper import PaperSettings, get_portfolio_summary
from funtrade.models.perturbation import (
    compute_perturbation_series,
    detect_latest_perturbations,
    signal_from_epsilon,
    trend_signal_kwargs,
)
from funtrade.paper.runner import run_paper_once

CHART_WINDOW_RECENT = "recent_120"
CHART_WINDOW_BACKTEST_TEST = "backtest_test"
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
) -> pd.DataFrame:
    """Latest model hints for every symbol in config.json (Nordnet manual trading)."""
    base = Settings.from_env()
    symbols = base.watchlist
    if not symbols:
        return pd.DataFrame()

    perturbations = detect_latest_perturbations(symbols=symbols, settings=base, persist=False)
    by_symbol = {p.symbol: p for p in perturbations}

    summary = get_portfolio_summary(settings=base, paper=params.to_paper_settings())
    positions = {pos["symbol"]: float(pos["net_qty_shares"]) for pos in summary.get("positions", [])}
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
                    "epsilon": None,
                    "threshold": sym_settings.epsilon_threshold,
                    "regime_valid": None,
                    "z_trend": None,
                    "position_shares": paper_qty,
                    "position_assumed": False,
                    "signal": None,
                    "action": "—",
                    "note": "No data (ingest + calibrate)",
                }
            )
            continue

        pos_assumed = assume_holding_all and paper_qty <= 0
        price = float(p.inputs.get("price", 0.0))
        assumed_qty = assumed_eur / price if price > 0 else assumed_eur / 100.0
        pos_qty = paper_qty if paper_qty > 0 else (assumed_qty if assume_holding_all else 0.0)
        z_trend = float(p.inputs.get("z_trend", 0.0))
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
                "price": float(p.inputs.get("price", 0.0)),
                "epsilon": round(p.epsilon, 3),
                "threshold": threshold,
                "regime_valid": p.regime_valid,
                "z_trend": round(z_trend, 2) if sym_settings.trend_enable else None,
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
    return df


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


def slice_perturbation_for_chart(
    series: pd.DataFrame,
    *,
    symbol: str,
    window: str,
    settings: Settings | None = None,
) -> pd.DataFrame:
    """Filter ε series for chart display (Trade tab vs backtest test window)."""
    if series.empty:
        return series
    if window == CHART_WINDOW_BACKTEST_TEST:
        test_start = backtest_test_start(symbol, settings=settings)
        if test_start is not None:
            sliced = series[series.index >= test_start]
            if not sliced.empty:
                return sliced
    return series.tail(120)


def perturbation_context(
    symbol: str,
    *,
    weights: tuple[float, float, float] | None = None,
    settings: Settings | None = None,
    h0_source: str = H0_SOURCE_SAVED,
) -> pd.DataFrame:
    base = settings or Settings.from_env()
    sym_settings = base.for_symbol(symbol)
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
    return {
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
        "h0_source": result.h0_source,
        "test_start": str(result.test_start) if result.test_start is not None else None,
        "equilibrium_half_life_days": result.equilibrium_half_life_days,
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
        "buy_and_hold_profit_eur": m.get("buy_and_hold_profit_eur", 0.0),
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
                "trade_signal": result.trade_signal.values,
            }
        ),
        "price_chart": pd.DataFrame(
            {"time": result.price.index, "price": result.price.values}
        ),
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
