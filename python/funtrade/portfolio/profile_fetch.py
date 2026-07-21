"""Fetch fund profiles from external sources and cache to fund_profiles/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from funtrade.config import Settings
from funtrade.portfolio.builtin_profiles import get_builtin_fund_profile
from funtrade.portfolio.eod_profiles import fetch_eod_etf_profile
from funtrade.portfolio.fund_profiles import FundProfile, load_fund_profile, save_fund_profile
from funtrade.portfolio.nordnet_profiles import fetch_nordnet_fund_profile
from funtrade.universe_config import AssetClassName, parse_asset_classes

SourceKind = Literal["auto", "eod", "nordnet", "static"]


def nordnet_slugs_path() -> Path:
    from funtrade.portfolio.fund_profiles import fund_profiles_dir

    return fund_profiles_dir() / "nordnet_slugs.json"


def load_nordnet_slugs() -> dict[str, str]:
    path = nordnet_slugs_path()
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: root must be a JSON object")
    return {
        str(k): str(v)
        for k, v in payload.items()
        if not str(k).startswith("_")
    }


def fetch_profile_for_symbol(
    symbol: str,
    *,
    settings: Settings | None = None,
    source: SourceKind = "auto",
    nordnet_slug: str | None = None,
) -> FundProfile:
    settings = settings or Settings.from_env()
    asset_class: AssetClassName = (
        settings.universe.class_of(symbol) if settings.universe else "etf"
    )

    if source == "static":
        prof = load_fund_profile(symbol)
        if prof is None:
            raise ValueError(f"No static fund profile for {symbol}")
        return prof

    builtin = get_builtin_fund_profile(symbol)
    slug = nordnet_slug or load_nordnet_slugs().get(symbol)

    if source == "nordnet" or (source == "auto" and asset_class == "mutual_fund"):
        if not slug:
            raise ValueError(
                f"No Nordnet slug for {symbol}. Add to fund_profiles/nordnet_slugs.json "
                "or pass --nordnet-url."
            )
        return fetch_nordnet_fund_profile(slug, symbol=symbol)

    if source == "eod" or (source == "auto" and asset_class == "etf"):
        if builtin is not None:
            return builtin
        try:
            return fetch_eod_etf_profile(symbol)
        except Exception:
            if slug and source == "auto":
                return fetch_nordnet_fund_profile(slug, symbol=symbol)
            raise

    if asset_class == "share":
        raise ValueError(f"Automatic profile fetch not supported for share {symbol}")

    raise ValueError(f"Unable to resolve profile source for {symbol} ({asset_class})")


def fetch_profiles(
    symbols: list[str],
    *,
    settings: Settings | None = None,
    source: SourceKind = "auto",
    overwrite: bool = True,
) -> dict[str, str | None]:
    """Fetch and save profiles. Returns symbol → path or error message."""
    settings = settings or Settings.from_env()
    slugs = load_nordnet_slugs()
    out: dict[str, str | None] = {}
    for symbol in symbols:
        try:
            profile = fetch_profile_for_symbol(
                symbol,
                settings=settings,
                source=source,
                nordnet_slug=slugs.get(symbol),
            )
            path = save_fund_profile(profile, overwrite=overwrite)
            out[symbol] = str(path)
        except Exception as exc:
            out[symbol] = str(exc)
    return out


def symbols_for_profile_fetch(
    settings: Settings | None = None,
    *,
    symbols: list[str] | None = None,
    asset_classes: str | list[str] | None = None,
) -> list[str]:
    settings = settings or Settings.from_env()
    if symbols:
        return symbols
    if asset_classes and settings.universe:
        return settings.universe.symbols_for_classes(parse_asset_classes(asset_classes))
    return list(settings.watchlist)
