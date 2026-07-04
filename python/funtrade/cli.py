"""Command-line entry points for funtrade."""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from funtrade.backtest.engine import (
    compare_to_buy_and_hold,
    export_backtest_report,
    run_backtest,
    walk_forward_threshold_sweep,
)
from funtrade.config import Settings
from funtrade.data.factors import ingest_macro_factors
from funtrade.data.ingest import ingest_watchlist
from funtrade.data.reconcile import reconcile_symbol
from funtrade.models.components import H0_COMPONENTS, H1_COMPONENTS
from funtrade.models.equilibrium import calibrate_equilibrium
from funtrade.models.perturbation import detect_latest_perturbations
from funtrade.sensitivity.jacobian import compute_jacobian, tune_weights_from_jacobian


def calibrate(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Calibrate H0 equilibrium model")
    parser.add_argument("--symbol", default="VWCE.DE")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=None)
    args = parser.parse_args(argv)

    end = pd.Timestamp(args.end, tz="UTC") if args.end else None
    model = calibrate_equilibrium(
        args.symbol,
        start=pd.Timestamp(args.start, tz="UTC"),
        end=end,
    )
    print(
        json.dumps(
            {
                "symbol": model.symbol,
                "kappa": model.kappa,
                "mu": model.mu,
                "sigma": model.sigma,
                "half_life_days": model.half_life_days,
                "seasonal_r_squared": model.seasonal_coeffs.get("r_squared"),
            },
            indent=2,
        )
    )


def detect(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Detect latest perturbations")
    parser.add_argument("--symbol", default=None)
    args = parser.parse_args(argv)

    symbols = [args.symbol] if args.symbol else None
    results = detect_latest_perturbations(symbols=symbols)
    print(
        json.dumps(
            [
                {
                    "symbol": r.symbol,
                    "time": r.time.isoformat(),
                    "epsilon": r.epsilon,
                    "magnitude": r.magnitude,
                    "regime_valid": r.regime_valid,
                    "inputs": r.inputs,
                }
                for r in results
            ],
            indent=2,
        )
    )


def backtest(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run perturbation backtest")
    parser.add_argument("--symbol", default="VWCE.DE")
    parser.add_argument("--threshold", type=float, default=2.0)
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)

    if args.report:
        export_backtest_report(
            args.symbol,
            args.report,
            sweep=args.sweep,
            epsilon_threshold=args.threshold,
        )
        print(f"Report written to {args.report}")
        return

    if args.sweep:
        df = walk_forward_threshold_sweep(args.symbol)
        print(df.to_string(index=False))
        return

    if args.compare:
        result = compare_to_buy_and_hold(args.symbol, epsilon_threshold=args.threshold)
        print(json.dumps(result, indent=2))
        return

    result = run_backtest(args.symbol, epsilon_threshold=args.threshold)
    print(
        json.dumps(
            {
                "symbol": result.symbol,
                "epsilon_threshold": result.epsilon_threshold,
                "sharpe": result.sharpe,
                "max_drawdown": result.max_drawdown,
                "hit_rate": result.hit_rate,
                "total_trades": result.total_trades,
                "total_return": result.total_return,
                "regime_invalidations": result.regime_invalidations,
            },
            indent=2,
        )
    )


def jacobian(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compute perturbation Jacobian")
    parser.add_argument("--symbol", default="VWCE.DE")
    parser.add_argument("--tune", action="store_true")
    args = parser.parse_args(argv)

    if args.tune:
        weights = tune_weights_from_jacobian(args.symbol)
        print(json.dumps({"suggested_weights": weights}, indent=2))
        return

    result = compute_jacobian(args.symbol)
    print(
        json.dumps(
            {
                "symbol": result.symbol,
                "jacobian": result.jacobian,
                "ranked_drivers": result.ranked_drivers,
                "suggested_weights": result.suggested_weights,
            },
            indent=2,
        )
    )


def ingest(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest watchlist price bars from Stooq")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--symbol", default=None)
    args = parser.parse_args(argv)

    symbols = [args.symbol] if args.symbol else None
    counts = ingest_watchlist(days=args.days, symbols=symbols)
    print(json.dumps({"rows_upserted": counts}, indent=2))


def ingest_factors(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest H0/H1 macro factor series")
    parser.add_argument("--days", type=int, default=730)
    args = parser.parse_args(argv)
    counts = ingest_macro_factors(days=args.days)
    print(json.dumps(counts, indent=2))


def reconcile(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Reconcile Stooq vs EOD prices")
    parser.add_argument("--symbol", default="VWCE.DE")
    args = parser.parse_args(argv)
    report = reconcile_symbol(args.symbol)
    print(
        json.dumps(
            {
                "symbol": report.symbol,
                "matched_days": report.matched_days,
                "mean_abs_diff_bps": report.mean_abs_diff_bps,
                "max_diff_bps": report.max_diff_bps,
                "agreement_rate": report.agreement_rate,
                "outliers": report.outliers,
            },
            indent=2,
        )
    )


def components(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="List H0/H1 component variables")
    parser.parse_args(argv)
    print(
        json.dumps(
            {
                "h0": [{"id": c.id, "name": c.name, "description": c.description} for c in H0_COMPONENTS],
                "h1": [{"id": c.id, "name": c.name, "description": c.description} for c in H1_COMPONENTS],
            },
            indent=2,
        )
    )


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: funtrade <calibrate|detect|backtest|...> [args]")
        sys.exit(1)

    commands = {
        "calibrate": calibrate,
        "detect": detect,
        "backtest": backtest,
        "jacobian": jacobian,
        "ingest": ingest,
        "ingest-factors": ingest_factors,
        "reconcile": reconcile,
        "components": components,
    }
    cmd = sys.argv[1]
    if cmd not in commands:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
    commands[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()
