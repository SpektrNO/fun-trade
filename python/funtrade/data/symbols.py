"""Watchlist symbol aliases: ISIN / friendly name → data-provider ticker (universe.json)."""

from __future__ import annotations

from dataclasses import dataclass

from funtrade.universe_config import load_universe_config


@dataclass(frozen=True)
class SymbolAlias:
    """Maps a watchlist id to the ticker used when fetching prices."""

    watchlist_id: str
    fetch_ticker: str
    name: str
    isin: str | None = None


# Built-in aliases with metadata (fetch ticker may be overridden in config.json).
BUILTIN_ALIASES: tuple[SymbolAlias, ...] = (
    SymbolAlias(
        watchlist_id="NO0010336977",
        fetch_ticker="0P00000O4C.IR",
        name="DNB Barnefond A",
        isin="NO0010336977",
    ),
    SymbolAlias(
        watchlist_id="DNB-BARNE.IR",
        fetch_ticker="0P00000O4C.IR",
        name="DNB Barnefond A",
        isin="NO0010336977",
    ),
    SymbolAlias(
        watchlist_id="AGGH.DE",
        fetch_ticker="EUNA.DE",
        name="iShares Core Global Aggregate Bond (Xetra AGGH → Yahoo EUNA)",
    ),
)


def symbol_aliases() -> dict[str, str]:
    """Upper-case watchlist id → fetch ticker (config.json aliases + builtins)."""
    merged = {a.watchlist_id.upper(): a.fetch_ticker for a in BUILTIN_ALIASES}
    try:
        merged.update(load_universe_config().aliases)
    except FileNotFoundError:
        pass
    return merged


def alias_catalog() -> list[dict]:
    """Builtin aliases plus config.json overrides for CLI / docs."""
    config_aliases = {}
    try:
        config_aliases = load_universe_config().aliases
    except FileNotFoundError:
        pass
    rows: list[dict] = []
    seen: set[str] = set()

    for alias in BUILTIN_ALIASES:
        key = alias.watchlist_id.upper()
        seen.add(key)
        rows.append(
            {
                "watchlist_id": alias.watchlist_id,
                "fetch_ticker": config_aliases.get(key, alias.fetch_ticker),
                "name": alias.name,
                "isin": alias.isin,
                "source": "config" if key in config_aliases else "builtin",
            }
        )

    for watchlist_id, fetch_ticker in config_aliases.items():
        if watchlist_id in seen:
            continue
        rows.append(
            {
                "watchlist_id": watchlist_id,
                "fetch_ticker": fetch_ticker,
                "name": watchlist_id,
                "isin": None,
                "source": "config",
            }
        )
    return rows


def resolve_fetch_ticker(symbol: str) -> str:
    """Resolve WATCHLIST symbol to the ticker passed to the price provider."""
    sym = symbol.strip()
    key = sym.upper()
    aliases = symbol_aliases()
    if key in aliases:
        return aliases[key]
    if key in ("EURUSD", "EURUSD=X"):
        return "EURUSD=X"
    # Yahoo futures / FX (e.g. BZ=F, CL=F) — do not append .DE
    if "=" in sym:
        return sym
    if "." not in sym:
        return f"{sym}.DE"
    return sym


def has_alias(symbol: str) -> bool:
    return symbol.strip().upper() in symbol_aliases()
