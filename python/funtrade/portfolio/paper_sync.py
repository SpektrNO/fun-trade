"""Mirror the paper trading wallet into portfolio.json."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from funtrade.config import Settings
from funtrade.execution.paper import PaperSettings, get_portfolio_summary
from funtrade.portfolio_config import portfolio_path, reset_portfolio_config_cache


def portfolio_sync_enabled() -> bool:
    return os.getenv("FUNTRADE_SYNC_PORTFOLIO", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def build_paper_wallet_portfolio_payload(summary: dict) -> dict:
    """Shape paper wallet summary as portfolio.json content."""
    holdings: list[dict] = []
    for pos in summary.get("positions", []):
        qty = float(pos["net_qty_shares"])
        if abs(qty) < 1e-12:
            continue
        mark = pos.get("mark_price")
        avg = float(pos.get("avg_price") or 0.0)
        price = float(mark) if mark is not None else avg
        value_eur = qty * price
        holdings.append(
            {
                "symbol": str(pos["symbol"]),
                "shares": round(qty, 6),
                "value_eur": round(value_eur, 2),
            }
        )

    holdings.sort(key=lambda row: row["symbol"])
    updated_at = summary.get("updated_at")
    if not updated_at:
        updated_at = datetime.now(UTC).isoformat()

    return {
        "name": "Paper wallet",
        "currency": "EUR",
        "valuation_mode": "weight_pct",
        "source": "paper_wallet",
        "updated_at": updated_at,
        "cash_eur": round(float(summary.get("cash_eur", 0.0)), 2),
        "holdings": holdings,
        "_comment": (
            "Auto-generated from the paper trading wallet. "
            "Updated on each virtual trade and wallet reset."
        ),
    }


def sync_portfolio_json_from_paper_wallet(
    *,
    settings: Settings | None = None,
    paper: PaperSettings | None = None,
    path: Path | str | None = None,
) -> Path | None:
    """Write portfolio.json from current paper positions (mark-to-market EUR)."""
    if not portfolio_sync_enabled():
        return None

    settings = settings or Settings.from_env()
    paper = paper or PaperSettings.from_env()
    target = Path(path) if path is not None else portfolio_path()
    summary = get_portfolio_summary(settings=settings, paper=paper)
    payload = build_paper_wallet_portfolio_payload(summary)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    reset_portfolio_config_cache()
    return target.resolve()
