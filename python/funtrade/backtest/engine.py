"""Event-driven backtest engine with walk-forward threshold calibration (daily bars)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from funtrade.config import Settings
from funtrade.data.loader import MARKET_ADJ_CLOSE, load_price_bars, save_backtest_run
from funtrade.models.equilibrium import calibrate_equilibrium
from funtrade.models.perturbation import compute_perturbation_series, signal_from_epsilon


def _backtest_config() -> tuple[float, float, float]:
    fee_bps = float(os.getenv("BACKTEST_FEE_BPS", "5"))
    limit = float(os.getenv("BACKTEST_POSITION_LIMIT_SHARES", "1000"))
    trade_shares = float(os.getenv("BACKTEST_TRADE_SHARES", "10"))
    return fee_bps, limit, trade_shares


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
    position_shares: pd.Series
    trade_volume_shares: pd.Series
    epsilon: pd.Series
    model_signal: pd.Series
    trade_signal: pd.Series
    regime_valid: pd.Series
    metrics: dict


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
    epsilon_threshold: float = 2.0,
    train_end: pd.Timestamp | None = None,
    test_start: pd.Timestamp | None = None,
    weights: tuple[float, float, float] = (0.35, 0.10, 0.25),
    fee_bps: float | None = None,
    position_limit_shares: float | None = None,
    trade_shares: float | None = None,
    settings: Settings | None = None,
    persist: bool = True,
) -> BacktestResult:
    settings = settings or Settings.from_env()

    all_data = load_price_bars(symbol, MARKET_ADJ_CLOSE, settings=settings)
    if all_data.empty or len(all_data) < 60:
        raise ValueError(f"No data for backtest in symbol {symbol}")

    if train_end is None:
        train_end = all_data.index[int(len(all_data) * 0.7)]
    if test_start is None:
        test_start = train_end

    train_start = all_data.index[0]

    equilibrium = calibrate_equilibrium(
        symbol,
        start=train_start,
        end=train_end,
        persist=False,
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

    price = test["price"].astype(float)
    price_change = price.diff().fillna(0.0)

    fee_bps_val, position_limit, trade_qty = _backtest_config()
    if fee_bps is not None:
        fee_bps_val = fee_bps
    if position_limit_shares is not None:
        position_limit = position_limit_shares
    if trade_shares is not None:
        trade_qty = trade_shares

    signals = pd.Series(0, index=test.index, dtype=int)
    model_signals = pd.Series(0, index=test.index, dtype=int)
    positions = pd.Series(0.0, index=test.index, dtype=float)
    trade_volumes = pd.Series(0.0, index=test.index, dtype=float)
    position = 0.0

    for i, (_ts, row) in enumerate(test.iterrows()):
        raw = signal_from_epsilon(
            float(row["epsilon"]),
            epsilon_threshold,
            bool(row["regime_valid"]),
            long_only=True,
            current_position=position,
        )
        model_signals.iloc[i] = raw
        traded = 0.0
        if raw != 0:
            delta = trade_qty if raw > 0 else -min(trade_qty, position)
            new_pos = position + delta
            if raw < 0 and position <= 0:
                new_pos = position
            if 0 <= new_pos <= position_limit:
                signals.iloc[i] = raw
                traded = abs(new_pos - position)
                position = new_pos
        positions.iloc[i] = position
        trade_volumes.iloc[i] = traded

    active_position = positions.shift(1).fillna(0.0)
    strategy_returns = active_position * price_change
    fees = trade_volumes * price * (fee_bps_val / 10000.0)
    strategy_returns = strategy_returns - fees

    metrics = _compute_metrics(strategy_returns, trade_volumes)
    regime_invalidations = int((~test["regime_valid"]).sum())

    equity = strategy_returns.cumsum()
    monthly_pnl = strategy_returns.resample("ME").sum().to_dict()
    monthly_pnl = {str(k): float(v) for k, v in monthly_pnl.items()}

    full_metrics = {
        **metrics,
        "regime_invalidations": regime_invalidations,
        "fee_bps": fee_bps_val,
        "position_limit_shares": position_limit,
        "trade_shares": trade_qty,
        "total_traded_shares": float(trade_volumes.sum()),
        "monthly_pnl": monthly_pnl,
    }

    vs_benchmark = None
    bench = load_price_bars(settings.benchmark, MARKET_ADJ_CLOSE, settings=settings)
    if not bench.empty:
        bench_test = bench["price"].astype(float).reindex(test.index, method="ffill")
        if len(bench_test.dropna()) > 1:
            bh_shares = 10000.0 / bench_test.iloc[0]
            bh_pnl = bh_shares * bench_test.diff().fillna(0.0)
            vs_benchmark = float(bh_pnl.sum()) - float(bh_pnl.iloc[0])

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
        position_shares=positions,
        trade_volume_shares=trade_volumes,
        epsilon=test["epsilon"].astype(float),
        model_signal=model_signals,
        trade_signal=signals,
        regime_valid=test["regime_valid"].astype(bool),
        metrics=full_metrics,
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
    thresholds = thresholds or [1.0, 1.5, 2.0, 2.5, 3.0]
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
        test_start = all_data.index[int(len(all_data) * 0.7)] if not all_data.empty else series.index[0]
    test = series[series.index >= test_start]
    price = test["price"].astype(float)

    bench = load_price_bars(settings.benchmark, MARKET_ADJ_CLOSE, settings=settings)
    bench_test = bench["price"].astype(float).reindex(test.index, method="ffill")
    bh_return = 0.0
    if len(bench_test.dropna()) > 1:
        bh_shares = 10000.0 / bench_test.dropna().iloc[0]
        bh_return = float((bh_shares * bench_test.diff().fillna(0.0)).sum())

    return {
        "symbol": symbol,
        "benchmark": settings.benchmark,
        "strategy_return": result.total_return,
        "buy_and_hold_benchmark_return": bh_return,
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
