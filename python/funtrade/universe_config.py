"""Universe and per-asset-class trading configuration (config.json)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

AssetClassName = Literal["etf", "mutual_fund", "share"]
ASSET_CLASSES: tuple[AssetClassName, ...] = ("etf", "mutual_fund", "share")


def _env_calibration_days() -> int:
    return int(os.getenv("H0_CALIBRATION_DAYS", "504"))


_DEFAULT_ETF: dict[str, Any] = {
    "symbols": ["EXSA.DE", "VWCE.DE", "EUNL.DE", "IS3N.DE", "SXR8.DE", "AGGH.DE", "IBCI.DE"],
    "h0_calibration_days": _env_calibration_days(),
    "epsilon_threshold": 0.75,
    "regime_spike_sigma": 3.0,
    "regime_consecutive_bars": 3,
    "min_daily_volume_eur": 100_000.0,
    "w_return": 0.35,
    "w_volume": 0.10,
    "w_rel_strength": 0.25,
    "trend_epsilon_weight": 0.15,
    "trend_fair_value_weight": 0.0,
    "trend_gate_sells": True,
    "trend_gate_z": 0.5,
    "trend_use_benchmark": False,
}

_DEFAULT_MUTUAL_FUND: dict[str, Any] = {
    "symbols": [],
    "h0_calibration_days": _env_calibration_days(),
    "epsilon_threshold": 0.75,
    "regime_spike_sigma": 3.0,
    "regime_consecutive_bars": 3,
    "min_daily_volume_eur": 0.0,
    "w_return": 0.35,
    "w_volume": 0.0,
    "w_rel_strength": 0.25,
    "trend_epsilon_weight": 0.15,
    "trend_fair_value_weight": 0.0,
    "trend_gate_sells": True,
    "trend_gate_z": 0.5,
    "trend_use_benchmark": True,
}

_DEFAULT_SHARE: dict[str, Any] = {
    "symbols": [],
    "h0_calibration_days": min(_env_calibration_days(), 365),
    "epsilon_threshold": 0.75,
    "regime_spike_sigma": 3.5,
    "regime_consecutive_bars": 3,
    "min_daily_volume_eur": 50_000.0,
    "w_return": 0.30,
    "w_volume": 0.15,
    "w_rel_strength": 0.25,
    "trend_epsilon_weight": 0.10,
    "trend_fair_value_weight": 0.0,
    "trend_gate_sells": False,
    "trend_gate_z": 0.5,
    "trend_use_benchmark": False,
}


@dataclass(frozen=True)
class AssetClassConfig:
    asset_class: AssetClassName
    symbols: tuple[str, ...]
    epsilon_threshold: float
    regime_spike_sigma: float
    regime_consecutive_bars: int
    min_daily_volume_eur: float
    w_return: float
    w_volume: float
    w_rel_strength: float
    trend_epsilon_weight: float
    trend_fair_value_weight: float
    trend_gate_sells: bool
    trend_gate_z: float
    trend_use_benchmark: bool
    h0_calibration_days: int

    def perturbation_weights(self) -> tuple[float, float, float]:
        return (self.w_return, self.w_volume, self.w_rel_strength)


@dataclass(frozen=True)
class UniverseConfig:
    benchmark: str
    currency: str
    aliases: dict[str, str]
    etf: AssetClassConfig
    mutual_fund: AssetClassConfig
    share: AssetClassConfig
    config_path: Path | None = None

    def watchlist(self) -> list[str]:
        return list(self.etf.symbols) + list(self.mutual_fund.symbols) + list(self.share.symbols)

    def class_of(self, symbol: str) -> AssetClassName:
        key = symbol.strip().upper()
        for name in ASSET_CLASSES:
            cfg = getattr(self, name)
            if key in {s.upper() for s in cfg.symbols}:
                return name
        return "etf"

    def for_symbol(self, symbol: str) -> AssetClassConfig:
        return getattr(self, self.class_of(symbol))

    def by_class(self) -> dict[AssetClassName, AssetClassConfig]:
        return {name: getattr(self, name) for name in ASSET_CLASSES}


_cached: UniverseConfig | None = None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def config_path() -> Path:
    raw = os.getenv("FUNTRADE_CONFIG", "config.json")
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


def _parse_asset_class(name: AssetClassName, raw: dict[str, Any] | None, defaults: dict[str, Any]) -> AssetClassConfig:
    data = {**defaults, **(raw or {})}
    symbols_raw = data.get("symbols", [])
    if isinstance(symbols_raw, str):
        symbols = tuple(s.strip() for s in symbols_raw.split(",") if s.strip())
    else:
        symbols = tuple(str(s).strip() for s in symbols_raw if str(s).strip())
    return AssetClassConfig(
        asset_class=name,
        symbols=symbols,
        epsilon_threshold=float(data["epsilon_threshold"]),
        regime_spike_sigma=float(data["regime_spike_sigma"]),
        regime_consecutive_bars=int(data["regime_consecutive_bars"]),
        min_daily_volume_eur=float(data["min_daily_volume_eur"]),
        w_return=float(data["w_return"]),
        w_volume=float(data["w_volume"]),
        w_rel_strength=float(data["w_rel_strength"]),
        trend_epsilon_weight=float(data["trend_epsilon_weight"]),
        trend_fair_value_weight=float(data["trend_fair_value_weight"]),
        trend_gate_sells=bool(data["trend_gate_sells"]),
        trend_gate_z=float(data["trend_gate_z"]),
        trend_use_benchmark=bool(data["trend_use_benchmark"]),
        h0_calibration_days=int(data.get("h0_calibration_days", _env_calibration_days())),
    )


def _parse_aliases(raw: Any) -> dict[str, str]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k).strip().upper(): str(v).strip() for k, v in raw.items()}
    raise ValueError("config.json aliases must be an object")


def load_universe_config(*, force_reload: bool = False) -> UniverseConfig:
    global _cached
    if _cached is not None and not force_reload:
        return _cached

    path = config_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"Universe config not found: {path}. Copy config.json.example to config.json "
            "(or set FUNTRADE_CONFIG)."
        )

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: root must be a JSON object")

    benchmark = str(payload.get("benchmark", "EXSA.DE"))
    currency = str(payload.get("currency", "EUR"))
    aliases = _parse_aliases(payload.get("aliases"))

    universe = UniverseConfig(
        benchmark=benchmark,
        currency=currency,
        aliases=aliases,
        etf=_parse_asset_class("etf", payload.get("etf"), _DEFAULT_ETF),
        mutual_fund=_parse_asset_class("mutual_fund", payload.get("mutual_fund"), _DEFAULT_MUTUAL_FUND),
        share=_parse_asset_class("share", payload.get("share"), _DEFAULT_SHARE),
        config_path=path,
    )
    _cached = universe
    return universe


def reset_universe_config_cache() -> None:
    global _cached
    _cached = None
