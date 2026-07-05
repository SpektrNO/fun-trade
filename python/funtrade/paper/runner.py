"""Forward paper trading: detect perturbations and simulate fills."""

from __future__ import annotations

import json
from argparse import ArgumentParser

from funtrade.config import Settings
from funtrade.data.loader import MARKET_ADJ_CLOSE, load_price_bars
from funtrade.execution.paper import PaperSettings, execute_trade, get_portfolio_summary
from funtrade.models.perturbation import detect_latest_perturbations, signal_from_epsilon, trend_signal_kwargs


def run_paper_once(
    symbols: list[str] | None = None,
    *,
    settings: Settings | None = None,
    paper: PaperSettings | None = None,
) -> list[dict]:
    settings = settings or Settings.from_env()
    paper = paper or PaperSettings.from_env()
    symbols = symbols or settings.watchlist
    results: list[dict] = []

    perturbations = detect_latest_perturbations(symbols=symbols, settings=settings, persist=True)

    for p in perturbations:
        sym_settings = settings.for_symbol(p.symbol)
        summary = get_portfolio_summary(settings=settings, paper=paper)
        pos_qty = 0.0
        for pos in summary.get("positions", []):
            if pos["symbol"] == p.symbol:
                pos_qty = pos["net_qty_shares"]
                break

        signal = signal_from_epsilon(
            p.epsilon,
            sym_settings.epsilon_threshold,
            p.regime_valid,
            long_only=True,
            current_position=pos_qty,
            **trend_signal_kwargs(sym_settings, float(p.inputs.get("z_trend", 0.0))),
        )
        price_df = load_price_bars(p.symbol, MARKET_ADJ_CLOSE, settings=sym_settings)
        if price_df.empty:
            continue

        price = float(p.inputs.get("price", price_df["price"].iloc[-1]))

        fill = execute_trade(
            signal,
            p.symbol,
            price,
            epsilon=p.epsilon,
            regime_valid=p.regime_valid,
            paper=paper,
            settings=settings,
        )

        results.append(
            {
                "symbol": p.symbol,
                "epsilon": p.epsilon,
                "signal": signal,
                "regime_valid": p.regime_valid,
                "fill": None
                if fill is None
                else {
                    "id": fill.id,
                    "side": fill.side,
                    "qty_shares": fill.qty_shares,
                    "price": fill.price,
                    "cash_after": fill.cash_after,
                },
            }
        )

    return results


def main(argv: list[str] | None = None) -> None:
    parser = ArgumentParser(description="Forward paper trading (simulated)")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    symbols = [args.symbol] if args.symbol else None
    results = run_paper_once(symbols=symbols)
    print(json.dumps(results, indent=2))

    if args.summary or not results:
        print(json.dumps(get_portfolio_summary(), indent=2))
