"""Event-driven backtest engine with walk-forward threshold calibration (daily bars)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from funtrade.config import Settings
from funtrade.data.loader import MARKET_ADJ_CLOSE, load_price_bars, normalize_daily_bars, save_backtest_run
from funtrade.execution.paper import PaperSettings, _fee_rate, _position_after_trade, compute_trade_qty
from funtrade.models.equilibrium import EquilibriumModel, calibrate_equilibrium, load_or_calibrate
from funtrade.models.perturbation import compute_perturbation_series, signal_from_epsilon, trend_signal_kwargs

H0_SOURCE_SAVED = "saved"
H0_SOURCE_WALK_FORWARD = "walk_forward"


def backtest_train_test_split(index: pd.DatetimeIndex) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Default 70/30 walk-forward split (train_end == test_start)."""
    train_end = index[int(len(index) * 0.7)]
    return train_end, train_end


def resolve_h0_equilibrium(
    symbol: str,
    *,
    h0_source: str,
    all_data: pd.DataFrame,
    settings: Settings,
) -> EquilibriumModel:
    """Saved H₀ from DB (Trade tab default) or walk-forward fit on the train slice."""
    if h0_source == H0_SOURCE_SAVED:
        return load_or_calibrate(symbol, settings=settings)
    train_start = all_data.index[0]
    train_end, _ = backtest_train_test_split(all_data.index)
    return calibrate_equilibrium(
        symbol,
        start=train_start,
        end=train_end,
        persist=False,
        settings=settings,
    )


def _daily_last_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per calendar day (last bar) for daily backtest accounting."""
    return normalize_daily_bars(frame)


def _backtest_wallet_config() -> PaperSettings:
    """Backtest wallet sizing — defaults mirror paper trade slice rules."""
    return PaperSettings(
        initial_cash=float(
            os.getenv(
                "BACKTEST_INITIAL_CASH_EUR",
                os.getenv("PAPER_INITIAL_CASH_EUR", "100000"),
            )
        ),
        position_limit_shares=float(
            os.getenv(
                "BACKTEST_POSITION_LIMIT_SHARES",
                os.getenv("PAPER_POSITION_LIMIT_SHARES", "1000"),
            )
        ),
        fee_bps=float(os.getenv("BACKTEST_FEE_BPS", os.getenv("PAPER_FEE_BPS", "5"))),
        trade_slice_pct=float(
            os.getenv(
                "BACKTEST_TRADE_SLICE_PCT",
                os.getenv("PAPER_TRADE_SLICE_PCT", "0.10"),
            )
        ),
        csv_path=Path(os.devnull),
    )


@dataclass
class BacktestResult:
    symbol: str
    epsilon_threshold: float
    sharpe: float
    max_drawdown: float
    hit_rate: float
    total_trades: int
    total_return: float
    regime_invalidations: int
    equity_curve: pd.Series
    price: pd.Series
    fair_price: pd.Series
    fair_plus_perturbation: pd.Series
    position_shares: pd.Series
    trade_volume_shares: pd.Series
    epsilon: pd.Series
    model_signal: pd.Series
    trade_signal: pd.Series
    regime_valid: pd.Series
    metrics: dict
    realized_pnl: pd.Series
    unrealized_pnl: pd.Series
    h0_source: str = H0_SOURCE_WALK_FORWARD
    test_start: pd.Timestamp | None = None
    equilibrium_half_life_days: float | None = None


def buy_and_hold_from_prices(prices: pd.Series, initial_cash: float) -> dict:
    """Passive buy at test-period start, hold to end (benchmark for the strategy)."""
    if prices.empty or initial_cash <= 0:
        return {
            "profit_eur": 0.0,
            "first_price": 0.0,
            "last_price": 0.0,
            "return_pct": 0.0,
            "final_eur": float(initial_cash),
        }
    first = float(prices.iloc[0])
    last = float(prices.iloc[-1])
    if first <= 0:
        return {
            "profit_eur": 0.0,
            "first_price": first,
            "last_price": last,
            "return_pct": 0.0,
            "final_eur": float(initial_cash),
        }
    final = initial_cash * (last / first)
    profit = final - initial_cash
    return {
        "profit_eur": profit,
        "first_price": first,
        "last_price": last,
        "return_pct": (last / first - 1.0) * 100.0,
        "final_eur": final,
    }


def _compute_portfolio_metrics(
    portfolio_value: pd.Series,
    trade_volumes: pd.Series,
    *,
    initial_capital: float,
) -> dict:
    if portfolio_value.empty:
        return {
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "hit_rate": 0.0,
            "total_trades": 0,
            "total_return": 0.0,
            "net_profit_eur": 0.0,
            "initial_capital_eur": initial_capital,
            "final_portfolio_eur": initial_capital,
            "return_pct": 0.0,
        }

    final_value = float(portfolio_value.iloc[-1])
    net_profit = final_value - initial_capital
    return_pct = (net_profit / initial_capital * 100.0) if initial_capital > 0 else 0.0

    rolling_max = portfolio_value.cummax()
    drawdown = portfolio_value - rolling_max
    max_drawdown = float(drawdown.min()) if len(drawdown) else 0.0

    daily_ret = portfolio_value.pct_change().fillna(0.0)
    std = daily_ret.std()
    sharpe = float(daily_ret.mean() / std * np.sqrt(252)) if std > 1e-9 else 0.0

    trade_days = trade_volumes > 0
    trade_pnl = daily_ret[trade_days] * portfolio_value.shift(1)[trade_days]
    hit_rate = float((trade_pnl > 0).mean()) if len(trade_pnl) else 0.0
    total_trades = int(trade_days.sum())

    return {
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "hit_rate": hit_rate,
        "total_trades": total_trades,
        "total_return": net_profit,
        "net_profit_eur": net_profit,
        "initial_capital_eur": initial_capital,
        "final_portfolio_eur": final_value,
        "return_pct": return_pct,
    }


def _compute_metrics(pnl: pd.Series, trades: pd.Series) -> dict:
    if pnl.empty:
        return {
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "hit_rate": 0.0,
            "total_trades": 0,
            "total_return": 0.0,
        }

    cumulative = pnl.cumsum()
    total_return = float(cumulative.iloc[-1]) if len(cumulative) else 0.0

    rolling_max = cumulative.cummax()
    drawdown = cumulative - rolling_max
    max_drawdown = float(drawdown.min()) if len(drawdown) else 0.0

    std = pnl.std()
    sharpe = float(pnl.mean() / std * np.sqrt(252)) if std > 1e-9 else 0.0

    trade_pnl = pnl[trades > 0] if np.issubdtype(trades.dtype, np.floating) else pnl[trades != 0]
    hit_rate = float((trade_pnl > 0).mean()) if len(trade_pnl) else 0.0
    total_trades = int((trades > 0).sum()) if np.issubdtype(trades.dtype, np.floating) else 0

    return {
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "hit_rate": hit_rate,
        "total_trades": total_trades,
        "total_return": total_return,
    }


def run_backtest(
    symbol: str,
    *,
    epsilon_threshold: float | None = None,
    train_end: pd.Timestamp | None = None,
    test_start: pd.Timestamp | None = None,
    weights: tuple[float, float, float] | None = None,
    fee_bps: float | None = None,
    position_limit_shares: float | None = None,
    trade_shares: float | None = None,
    trade_slice_pct: float | None = None,
    initial_cash_eur: float | None = None,
    settings: Settings | None = None,
    persist: bool = True,
    h0_source: str = H0_SOURCE_WALK_FORWARD,
) -> BacktestResult:
    if settings is None:
        settings = Settings.from_env().for_symbol(symbol)
    if epsilon_threshold is None:
        epsilon_threshold = settings.epsilon_threshold
    if weights is None:
        weights = settings.perturbation_weights()

    all_data = load_price_bars(symbol, MARKET_ADJ_CLOSE, settings=settings)
    if all_data.empty or len(all_data) < 60:
        raise ValueError(f"No data for backtest in symbol {symbol}")

    default_train_end, default_test_start = backtest_train_test_split(all_data.index)
    if train_end is None:
        train_end = default_train_end
    if test_start is None:
        test_start = default_test_start

    equilibrium = resolve_h0_equilibrium(
        symbol,
        h0_source=h0_source,
        all_data=all_data,
        settings=settings,
    )

    series = compute_perturbation_series(
        symbol,
        weights=weights,
        equilibrium=equilibrium,
        settings=settings,
    )
    if series.empty:
        raise ValueError(f"No perturbation series for {symbol}")

    test = series[series.index >= test_start].copy()
    if test.empty:
        raise ValueError(f"No test-period data after {test_start}")

    test = _daily_last_bars(test)

    price = test["price"].astype(float)
    z_trend = test["z_trend"] if "z_trend" in test.columns else None
    fair_price = (
        equilibrium.equilibrium_band(price, symbol=symbol, settings=settings, z_trend=z_trend)["equilibrium"]
        .astype(float)
    )
    epsilon_series = test["epsilon"].astype(float)
    fair_plus_perturbation = np.exp(
        np.log(fair_price.clip(lower=1e-6)) + epsilon_series * equilibrium.sigma
    )

    wallet = _backtest_wallet_config()
    if fee_bps is not None:
        wallet = replace(wallet, fee_bps=fee_bps)
    if position_limit_shares is not None:
        wallet = replace(wallet, position_limit_shares=position_limit_shares)
    if trade_slice_pct is not None:
        wallet = replace(wallet, trade_slice_pct=trade_slice_pct)
    if initial_cash_eur is not None:
        wallet = replace(wallet, initial_cash=initial_cash_eur)
    initial_cash = wallet.initial_cash
    fixed_qty = trade_shares

    signals = pd.Series(0, index=test.index, dtype=int)
    model_signals = pd.Series(0, index=test.index, dtype=int)
    positions = pd.Series(0.0, index=test.index, dtype=float)
    cash_series = pd.Series(initial_cash, index=test.index, dtype=float)
    portfolio_value = pd.Series(initial_cash, index=test.index, dtype=float)
    trade_volumes = pd.Series(0.0, index=test.index, dtype=float)
    cash = initial_cash
    position = 0.0
    avg_cost = 0.0
    realized_pnl = 0.0
    total_fees = 0.0
    realized_series = pd.Series(0.0, index=test.index, dtype=float)
    unrealized_series = pd.Series(0.0, index=test.index, dtype=float)

    for i, (_ts, row) in enumerate(test.iterrows()):
        bar_price = float(price.iloc[i])
        raw = signal_from_epsilon(
            float(row["epsilon"]),
            epsilon_threshold,
            bool(row["regime_valid"]),
            long_only=True,
            current_position=position,
            **trend_signal_kwargs(settings, float(row.get("z_trend", 0.0))),
        )
        model_signals.iloc[i] = raw
        traded = 0.0
        if raw != 0:
            side = "buy" if raw > 0 else "sell"
            delta = compute_trade_qty(
                side=side,
                price=bar_price,
                cash_eur=cash,
                net_qty=position,
                paper=wallet,
                epsilon=float(row["epsilon"]),
                epsilon_threshold=epsilon_threshold,
                qty_shares=fixed_qty,
            )
            if delta > 0:
                if raw > 0:
                    cost = delta * bar_price
                    fee = cost * _fee_rate(wallet.fee_bps)
                    position, avg_cost, realized_delta = _position_after_trade(
                        position, avg_cost, "buy", delta, bar_price
                    )
                    realized_pnl += realized_delta
                    cash -= cost + fee
                    total_fees += fee
                    signals.iloc[i] = raw
                    traded = delta
                else:
                    proceeds = delta * bar_price
                    fee = proceeds * _fee_rate(wallet.fee_bps)
                    position, avg_cost, realized_delta = _position_after_trade(
                        position, avg_cost, "sell", delta, bar_price
                    )
                    realized_pnl += realized_delta
                    cash += proceeds - fee
                    total_fees += fee
                    signals.iloc[i] = raw
                    traded = delta
        positions.iloc[i] = position
        cash_series.iloc[i] = cash
        trade_volumes.iloc[i] = traded
        portfolio_value.iloc[i] = cash + position * bar_price
        realized_series.iloc[i] = realized_pnl
        unrealized_series.iloc[i] = (
            position * (bar_price - avg_cost) if position > 0 else 0.0
        )

    last_price = float(price.iloc[-1])
    unrealized_pnl = position * (last_price - avg_cost) if position > 0 else 0.0
    total_pnl = realized_pnl + unrealized_pnl

    metrics = _compute_portfolio_metrics(
        portfolio_value,
        trade_volumes,
        initial_capital=initial_cash,
    )
    regime_invalidations = int((~test["regime_valid"]).sum())

    equity = portfolio_value
    monthly_pnl = portfolio_value.diff().fillna(0.0).resample("ME").sum().to_dict()
    monthly_pnl = {str(k): float(v) for k, v in monthly_pnl.items()}

    bh = buy_and_hold_from_prices(price, initial_cash)

    full_metrics = {
        **metrics,
        "regime_invalidations": regime_invalidations,
        "fee_bps": wallet.fee_bps,
        "position_limit_shares": wallet.position_limit_shares,
        "trade_slice_pct": wallet.trade_slice_pct,
        "total_traded_shares": float(trade_volumes.sum()),
        "total_fees_eur": total_fees,
        "realized_pnl_eur": realized_pnl,
        "unrealized_pnl_eur": unrealized_pnl,
        "total_pnl_eur": total_pnl,
        "avg_cost_eur": avg_cost if position > 0 else 0.0,
        "final_cash_eur": float(cash_series.iloc[-1]),
        "final_shares": float(positions.iloc[-1]),
        "buy_and_hold_first_price": bh["first_price"],
        "buy_and_hold_last_price": bh["last_price"],
        "buy_and_hold_return_pct": bh["return_pct"],
        "buy_and_hold_final_eur": bh["final_eur"],
        "buy_and_hold_profit_eur": bh["profit_eur"],
        "monthly_pnl": monthly_pnl,
    }

    vs_benchmark = None
    bench = load_price_bars(settings.benchmark, MARKET_ADJ_CLOSE, settings=settings)
    if not bench.empty:
        bench_test = bench["price"].astype(float).reindex(test.index, method="ffill")
        if len(bench_test.dropna()) > 1:
            bench_first = float(bench_test.dropna().iloc[0])
            bench_last = float(bench_test.dropna().iloc[-1])
            if bench_first > 0:
                bench_shares = initial_cash / bench_first
                vs_benchmark = bench_shares * bench_last - initial_cash

    result = BacktestResult(
        symbol=symbol,
        epsilon_threshold=epsilon_threshold,
        sharpe=metrics["sharpe"],
        max_drawdown=metrics["max_drawdown"],
        hit_rate=metrics["hit_rate"],
        total_trades=metrics["total_trades"],
        total_return=metrics["total_return"],
        regime_invalidations=regime_invalidations,
        equity_curve=equity,
        price=price,
        fair_price=fair_price,
        fair_plus_perturbation=fair_plus_perturbation,
        position_shares=positions,
        trade_volume_shares=trade_volumes,
        epsilon=test["epsilon"].astype(float),
        model_signal=model_signals,
        trade_signal=signals,
        regime_valid=test["regime_valid"].astype(bool),
        metrics=full_metrics,
        realized_pnl=realized_series,
        unrealized_pnl=unrealized_series,
        h0_source=h0_source,
        test_start=test_start,
        equilibrium_half_life_days=float(getattr(equilibrium, "half_life_days", 0.0) or 0.0),
    )

    if persist:
        save_backtest_run(
            symbol,
            epsilon_threshold,
            full_metrics,
            benchmark_symbol=settings.benchmark,
            vs_benchmark_return=vs_benchmark,
            settings=settings,
        )

    return result


def walk_forward_threshold_sweep(
    symbol: str,
    thresholds: list[float] | None = None,
    **kwargs,
) -> pd.DataFrame:
    thresholds = thresholds or [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
    rows = []
    for threshold in thresholds:
        try:
            result = run_backtest(symbol, epsilon_threshold=threshold, persist=False, **kwargs)
            rows.append(
                {
                    "threshold": threshold,
                    "sharpe": result.sharpe,
                    "max_drawdown": result.max_drawdown,
                    "hit_rate": result.hit_rate,
                    "total_trades": result.total_trades,
                    "total_return": result.total_return,
                    "regime_invalidations": result.regime_invalidations,
                }
            )
        except ValueError:
            continue
    return pd.DataFrame(rows)


def compare_to_buy_and_hold(symbol: str, **kwargs) -> dict:
    settings = Settings.from_env()
    result = run_backtest(symbol, persist=False, **kwargs)
    series = compute_perturbation_series(symbol, settings=settings)
    test_start = kwargs.get("test_start")
    if test_start is None:
        all_data = load_price_bars(symbol, MARKET_ADJ_CLOSE)
        if not all_data.empty:
            _, test_start = backtest_train_test_split(all_data.index)
        else:
            test_start = series.index[0]
    test = series[series.index >= test_start]
    price = test["price"].astype(float)
    initial_cash = _backtest_wallet_config().initial_cash

    bench = load_price_bars(settings.benchmark, MARKET_ADJ_CLOSE, settings=settings)
    bench_test = bench["price"].astype(float).reindex(test.index, method="ffill")
    bh_return = 0.0
    if len(bench_test.dropna()) > 1:
        bench_first = float(bench_test.dropna().iloc[0])
        bench_last = float(bench_test.dropna().iloc[-1])
        if bench_first > 0:
            bh_return = (initial_cash / bench_first) * bench_last - initial_cash

    return {
        "symbol": symbol,
        "benchmark": settings.benchmark,
        "initial_capital_eur": initial_cash,
        "strategy_net_profit_eur": result.metrics.get("net_profit_eur", result.total_return),
        "strategy_final_portfolio_eur": result.metrics.get("final_portfolio_eur"),
        "strategy_return_pct": result.metrics.get("return_pct", 0.0),
        "strategy_return": result.total_return,
        "buy_and_hold_benchmark_profit_eur": bh_return,
        "strategy_sharpe": result.sharpe,
        "strategy_max_drawdown": result.max_drawdown,
    }


def export_backtest_report(
    symbol: str,
    path: str | Path,
    *,
    sweep: bool = False,
    **kwargs,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if sweep:
        df = walk_forward_threshold_sweep(symbol, **kwargs)
        payload = {"symbol": symbol, "sweep": df.to_dict(orient="records")}
    else:
        result = run_backtest(symbol, persist=False, **kwargs)
        payload = {
            "symbol": symbol,
            "epsilon_threshold": result.epsilon_threshold,
            "metrics": result.metrics,
            "equity_curve": {str(k): float(v) for k, v in result.equity_curve.items()},
        }

    path.write_text(json.dumps(payload, indent=2, default=str))
