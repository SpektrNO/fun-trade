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
from funtrade.data.symbols import alias_catalog
from funtrade.models.components import ALL_H0_COMPONENTS, H1_COMPONENTS, OPTIONAL_H0_COMPONENTS
from funtrade.models.equilibrium import calibrate_equilibrium
from funtrade.models.perturbation import detect_latest_perturbations
from funtrade.sensitivity.jacobian import compute_jacobian, tune_weights_from_jacobian


def _calibrate_summary(model, *, asset_class: str | None = None, h0_calibration_days: int | None = None) -> dict:
    out = {
        "symbol": model.symbol,
        "asset_class": asset_class,
        "h0_calibration_days": h0_calibration_days,
        "kappa": model.kappa,
        "mu": model.mu,
        "sigma": model.sigma,
        "half_life_days": model.half_life_days,
        "seasonal_r_squared": model.seasonal_coeffs.get("r_squared"),
        "seasonal_dow": model.seasonal_coeffs.get("seasonal_dow"),
    }
    return {k: v for k, v in out.items() if v is not None}


def calibrate(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Calibrate H0 equilibrium model")
    parser.add_argument("--symbol", default=None, help="Single symbol (default: VWCE.DE)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Calibrate every symbol in WATCHLIST",
    )
    parser.add_argument("--start", default=None, help="Calibration window start (default: H0_CALIBRATION_DAYS lookback)")
    parser.add_argument("--end", default=None)
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.all:
        symbols = settings.watchlist
    elif args.symbol:
        symbols = [args.symbol]
    else:
        symbols = ["VWCE.DE"]

    start = pd.Timestamp(args.start, tz="UTC") if args.start else None
    end = pd.Timestamp(args.end, tz="UTC") if args.end else None

    calibrated: list[dict] = []
    errors: dict[str, str] = {}
    for symbol in symbols:
        try:
            sym_settings = settings.for_symbol(symbol)
            model = calibrate_equilibrium(symbol, start=start, end=end, settings=sym_settings)
            calibrated.append(
                _calibrate_summary(
                    model,
                    asset_class=sym_settings.asset_class,
                    h0_calibration_days=sym_settings.h0_calibration_days,
                )
            )
        except Exception as exc:
            errors[symbol] = str(exc)

    payload: dict = {"calibrated": calibrated}
    if errors:
        payload["errors"] = errors
    print(json.dumps(payload, indent=2))
    if errors and not calibrated:
        sys.exit(1)


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
                    "asset_class": r.asset_class,
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
    m = result.metrics
    print(
        json.dumps(
            {
                "symbol": result.symbol,
                "epsilon_threshold": result.epsilon_threshold,
                "initial_capital_eur": m.get("initial_capital_eur"),
                "final_portfolio_eur": m.get("final_portfolio_eur"),
                "net_profit_eur": m.get("net_profit_eur", result.total_return),
                "return_pct": m.get("return_pct"),
                "realized_pnl_eur": m.get("realized_pnl_eur"),
                "unrealized_pnl_eur": m.get("unrealized_pnl_eur"),
                "total_pnl_eur": m.get("total_pnl_eur"),
                "total_fees_eur": m.get("total_fees_eur"),
                "buy_and_hold_profit_eur": m.get("buy_and_hold_profit_eur"),
                "sharpe": result.sharpe,
                "max_drawdown": result.max_drawdown,
                "hit_rate": result.hit_rate,
                "total_trades": result.total_trades,
                "final_cash_eur": m.get("final_cash_eur"),
                "final_shares": m.get("final_shares"),
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


def _resolve_ingest_symbols(*, symbol: str | None, symbols: list[str] | None) -> list[str] | None:
    """One symbol, explicit list, or None for full watchlist."""
    if symbols:
        return symbols
    if symbol:
        return [symbol]
    return None


def ingest(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest watchlist price bars from Stooq")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--symbol", default=None, help="Single watchlist symbol")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        metavar="SYMBOL",
        help="Whitespace-separated symbols (e.g. --symbols VWCE.DE EXSA.DE)",
    )
    args = parser.parse_args(argv)

    resolved = _resolve_ingest_symbols(symbol=args.symbol, symbols=args.symbols)
    counts = ingest_watchlist(days=args.days, symbols=resolved)
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


def symbols(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="List WATCHLIST symbol aliases (ISIN / friendly name → fetch ticker)")
    parser.parse_args(argv)
    settings = Settings.from_env()
    rows = []
    for alias in alias_catalog():
        watchlist_id = alias["watchlist_id"]
        asset_class = settings.universe.class_of(watchlist_id) if settings.universe else "etf"
        rows.append(
            {
                **alias,
                "asset_class": asset_class,
                "in_watchlist": watchlist_id in settings.watchlist
                or watchlist_id.upper() in {s.upper() for s in settings.watchlist},
            }
        )
    print(json.dumps({"aliases": rows, "watchlist": settings.watchlist, "by_class": {
        name: list(getattr(settings.universe, name).symbols) if settings.universe else []
        for name in ("etf", "mutual_fund", "share")
    }}, indent=2))


def components(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="List H0/H1 component variables")
    parser.parse_args(argv)
    settings = Settings.from_env()
    weights = settings.h0_weights()
    active_ids = set(settings.active_h0_component_ids())
    optional_ids = {c.id for c in OPTIONAL_H0_COMPONENTS}
    print(
        json.dumps(
            {
                "h0": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "description": c.description,
                        "enabled": c.id in active_ids,
                        "optional": c.id in optional_ids,
                        "weight": weights.get(c.id),
                    }
                    for c in ALL_H0_COMPONENTS
                ],
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
        "symbols": symbols,
    }
    cmd = sys.argv[1]
    if cmd not in commands:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
    commands[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()
