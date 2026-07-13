"""Real portfolio holdings (portfolio.json) — separate from trading config.json."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from funtrade.universe_config import repo_root

PortfolioValuationMode = Literal["weight_pct", "shares", "value_eur"]

_DEFAULT_VALUATION_MODE: PortfolioValuationMode = "weight_pct"


@dataclass(frozen=True)
class PortfolioHolding:
    symbol: str
    weight_pct: float | None = None
    shares: float | None = None
    value_eur: float | None = None
    note: str | None = None


@dataclass(frozen=True)
class PortfolioConfig:
    """User holdings for look-through allocation (not the paper trading wallet)."""

    name: str
    currency: str
    valuation_mode: PortfolioValuationMode
    holdings: tuple[PortfolioHolding, ...]
    source_path: Path | None = None

    def symbols(self) -> tuple[str, ...]:
        return tuple(h.symbol for h in self.holdings)

    def total_weight_pct(self) -> float:
        return sum(h.weight_pct or 0.0 for h in self.holdings)


_UNSET = object()
_cached: PortfolioConfig | None | object = _UNSET


def portfolio_path() -> Path:
    raw = os.getenv("FUNTRADE_PORTFOLIO", "portfolio.json")
    path = Path(raw)
    if path.is_file():
        return path.resolve()
    if path.is_absolute():
        return path
    for base in (Path.cwd(), repo_root()):
        candidate = (base / raw).resolve()
        if candidate.is_file():
            return candidate
    return (repo_root() / raw).resolve()


def _parse_valuation_mode(raw: str | None) -> PortfolioValuationMode:
    mode = str(raw or _DEFAULT_VALUATION_MODE).strip().lower()
    if mode not in ("weight_pct", "shares", "value_eur"):
        raise ValueError(
            f"portfolio.json valuation_mode must be weight_pct, shares, or value_eur (got {mode!r})"
        )
    return mode  # type: ignore[return-value]


def _parse_holding(raw: dict, *, valuation_mode: PortfolioValuationMode) -> PortfolioHolding:
    symbol = str(raw.get("symbol", "")).strip()
    if not symbol:
        raise ValueError("portfolio holding missing symbol")

    weight_pct = raw.get("weight_pct")
    shares = raw.get("shares")
    value_eur = raw.get("value_eur")
    note = raw.get("note")
    note_str = str(note).strip() if note else None

    w = float(weight_pct) if weight_pct is not None else None
    sh = float(shares) if shares is not None else None
    val = float(value_eur) if value_eur is not None else None

    if valuation_mode == "weight_pct" and w is None:
        raise ValueError(f"portfolio holding {symbol!r}: weight_pct required when valuation_mode=weight_pct")
    if valuation_mode == "shares" and sh is None:
        raise ValueError(f"portfolio holding {symbol!r}: shares required when valuation_mode=shares")
    if valuation_mode == "value_eur" and val is None:
        raise ValueError(f"portfolio holding {symbol!r}: value_eur required when valuation_mode=value_eur")

    return PortfolioHolding(symbol=symbol, weight_pct=w, shares=sh, value_eur=val, note=note_str)


def load_portfolio_config(*, force_reload: bool = False) -> PortfolioConfig | None:
    """Load portfolio.json if present; None when file does not exist yet."""
    global _cached
    if _cached is not _UNSET and not force_reload:
        return _cached  # type: ignore[return-value]

    path = portfolio_path()
    if not path.is_file():
        _cached = None
        return None

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: root must be a JSON object")

    valuation_mode = _parse_valuation_mode(payload.get("valuation_mode"))
    holdings_raw = payload.get("holdings", [])
    if not isinstance(holdings_raw, list):
        raise ValueError(f"{path}: holdings must be an array")

    holdings: list[PortfolioHolding] = []
    for i, item in enumerate(holdings_raw):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: holdings[{i}] must be an object")
        holdings.append(_parse_holding(item, valuation_mode=valuation_mode))

    cfg = PortfolioConfig(
        name=str(payload.get("name", "Portfolio")),
        currency=str(payload.get("currency", "EUR")),
        valuation_mode=valuation_mode,
        holdings=tuple(holdings),
        source_path=path,
    )
    _cached = cfg
    return cfg


def reset_portfolio_config_cache() -> None:
    global _cached
    _cached = _UNSET
