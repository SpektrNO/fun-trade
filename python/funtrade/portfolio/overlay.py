"""Portfolio-scoped ε hints — rank adds/trims among holdings (no target weights)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class OverlayAction:
    label: str
    priority: int
    detail: str


def overlay_action_for_holding(
    *,
    epsilon: float | None,
    threshold: float,
    regime_valid: bool | None,
    add_rank: int | None = None,
    trim_rank: int | None = None,
) -> OverlayAction:
    """ε-only overlay label for one holding (long-only perturbation)."""
    if epsilon is None:
        return OverlayAction("—", 0, "No ε data")

    deep_buy = epsilon < -threshold and bool(regime_valid)
    rich = epsilon > threshold

    if deep_buy and add_rank is not None:
        return OverlayAction(
            "Add (dip)",
            2,
            f"#{add_rank} most depressed vs fair among your holdings (ε={epsilon:.2f})",
        )
    if rich and trim_rank is not None:
        return OverlayAction(
            "Trim (rich)",
            2,
            f"#{trim_rank} richest vs fair among your holdings (ε={epsilon:.2f})",
        )
    if abs(epsilon) <= threshold:
        return OverlayAction("Hold", 0, "Inside ε band — stay on plan")
    if epsilon < -threshold and not regime_valid:
        return OverlayAction("Hold", 0, "Buy blocked (regime invalid)")
    if deep_buy:
        return OverlayAction("Hold", 0, "Cheap vs fair, but not in top dip ranks")
    if rich:
        return OverlayAction("Hold", 0, "Rich vs fair, but not in top trim ranks")
    return OverlayAction("Hold", 0, "No overlay action")


def build_portfolio_overlay(
    recommendations: pd.DataFrame,
    *,
    max_adds: int = 3,
    max_trims: int = 2,
) -> pd.DataFrame:
    """Keep only the strongest ε-ranked add/trim hints among portfolio holdings."""
    if recommendations.empty:
        return recommendations

    work = recommendations.copy()
    if "in_portfolio" in work.columns:
        portfolio_rows = work[work["in_portfolio"]].copy()
        if not portfolio_rows.empty:
            work = portfolio_rows
    if work.empty:
        return work
    work["_add_rank"] = pd.NA
    work["_trim_rank"] = pd.NA

    buy_pool = work[
        work["epsilon"].notna()
        & work["regime_valid"].fillna(False)
        & (work["epsilon"] < work["threshold"])
    ].sort_values("epsilon")
    for rank, idx in enumerate(buy_pool.head(max_adds).index, start=1):
        work.loc[idx, "_add_rank"] = rank

    sell_pool = work[work["epsilon"].notna() & (work["epsilon"] > work["threshold"])].sort_values(
        "epsilon", ascending=False,
    )
    for rank, idx in enumerate(sell_pool.head(max_trims).index, start=1):
        work.loc[idx, "_trim_rank"] = rank

    rows: list[dict] = []
    for _, rec in work.iterrows():
        add_rank = rec.get("_add_rank")
        trim_rank = rec.get("_trim_rank")
        action = overlay_action_for_holding(
            epsilon=rec.get("epsilon") if pd.notna(rec.get("epsilon")) else None,
            threshold=float(rec.get("threshold") or 0.75),
            regime_valid=rec.get("regime_valid") if pd.notna(rec.get("regime_valid")) else None,
            add_rank=int(add_rank) if pd.notna(add_rank) else None,
            trim_rank=int(trim_rank) if pd.notna(trim_rank) else None,
        )
        row = rec.to_dict()
        row.pop("_add_rank", None)
        row.pop("_trim_rank", None)
        row["overlay_action"] = action.label
        row["overlay_priority"] = action.priority
        row["overlay_detail"] = action.detail
        rows.append(row)

    out = pd.DataFrame(rows)
    actionable = out[out["overlay_priority"] > 0]
    return actionable.sort_values(["overlay_priority", "epsilon"], ascending=[False, True], na_position="last")
