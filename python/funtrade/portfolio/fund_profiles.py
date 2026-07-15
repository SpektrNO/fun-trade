"""Load static fund composition profiles from fund_profiles/*.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from funtrade.universe_config import repo_root


@dataclass(frozen=True)
class FundProfile:
    symbol: str
    name: str
    as_of: str
    source: str
    regions: dict[str, float]
    sectors: dict[str, float]
    asset_classes: dict[str, float]

    def __post_init__(self) -> None:
        for label, bucket in (
            ("regions", self.regions),
            ("sectors", self.sectors),
            ("asset_classes", self.asset_classes),
        ):
            total = sum(bucket.values())
            if bucket and abs(total - 1.0) > 0.05:
                raise ValueError(
                    f"{self.symbol} {label} weights sum to {total:.3f}, expected ~1.0"
                )


def fund_profiles_dir() -> Path:
    return repo_root() / "fund_profiles"


def _parse_weight_map(raw: object, *, field: str, symbol: str) -> dict[str, float]:
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{symbol}: {field} must be an object")
    out: dict[str, float] = {}
    for key, val in raw.items():
        name = str(key).strip()
        if not name:
            continue
        out[name] = float(val)
    return out


def _parse_profile(payload: dict, *, path: Path) -> FundProfile:
    symbol = str(payload.get("symbol", path.stem)).strip()
    if not symbol:
        raise ValueError(f"{path}: missing symbol")
    return FundProfile(
        symbol=symbol,
        name=str(payload.get("name", symbol)),
        as_of=str(payload.get("as_of", "unknown")),
        source=str(payload.get("source", "manual")),
        regions=_parse_weight_map(payload.get("regions"), field="regions", symbol=symbol),
        sectors=_parse_weight_map(payload.get("sectors"), field="sectors", symbol=symbol),
        asset_classes=_parse_weight_map(
            payload.get("asset_classes"), field="asset_classes", symbol=symbol,
        ),
    )


def load_fund_profile(symbol: str) -> FundProfile | None:
    """Load fund_profiles/{symbol}.json if present."""
    sym = symbol.strip()
    if not sym:
        return None
    base = fund_profiles_dir()
    candidates = [sym, sym.upper()]
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        path = base / f"{candidate}.json"
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{path}: root must be a JSON object")
        return _parse_profile(payload, path=path)
    return None


def save_fund_profile(profile: FundProfile, *, overwrite: bool = True) -> Path:
    """Write fund_profiles/{symbol}.json."""
    base = fund_profiles_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{profile.symbol}.json"
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))
    path.write_text(
        json.dumps(
            {
                "symbol": profile.symbol,
                "name": profile.name,
                "as_of": profile.as_of,
                "source": profile.source,
                "regions": profile.regions,
                "sectors": profile.sectors,
                "asset_classes": profile.asset_classes,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def list_fund_profile_symbols() -> list[str]:
    base = fund_profiles_dir()
    if not base.is_dir():
        return []
    out: list[str] = []
    for path in sorted(base.glob("*.json")):
        if path.name.upper() == "README.JSON":
            continue
        out.append(path.stem)
    return out
