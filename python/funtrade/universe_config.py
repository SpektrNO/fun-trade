"""Universe and per-asset-class trading configuration (config.json)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

AssetClassName = Literal["etf", "mutual_fund", "share"]
MomentumPositionMode = Literal["slice", "scale", "full"]
RsiMode = Literal["momentum", "mean_reversion"]
ASSET_CLASSES: tuple[AssetClassName, ...] = ("etf", "mutual_fund", "share")

_ASSET_CLASS_ALIASES: dict[str, AssetClassName] = {
    "etf": "etf",
    "etfs": "etf",
    "mutual_fund": "mutual_fund",
    "mutual_funds": "mutual_fund",
    "mutual": "mutual_fund",
    "fund": "mutual_fund",
    "funds": "mutual_fund",
    "share": "share",
    "shares": "share",
    "stock": "share",
    "stocks": "share",
}


def parse_asset_classes(values: str | Iterable[str] | None) -> tuple[AssetClassName, ...]:
    """Parse CLI/Make CLASS values such as ``ETF SHARE`` or ``['etf', 'share']``."""
    if not values:
        return ()
    parts: list[str] = []
    if isinstance(values, str):
        parts.extend(values.split())
    else:
        for value in values:
            parts.extend(str(value).split())
    out: list[AssetClassName] = []
    seen: set[AssetClassName] = set()
    for part in parts:
        key = part.strip().lower().replace("-", "_")
        if not key:
            continue
        normalized = _ASSET_CLASS_ALIASES.get(key)
        if normalized is None:
            allowed = ", ".join(sorted({k for k, v in _ASSET_CLASS_ALIASES.items() if k == v}))
            raise ValueError(f"Unknown asset class {part!r}. Use: {allowed}")
        if normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return tuple(out)


def _env_calibration_days() -> int:
    return int(os.getenv("H0_CALIBRATION_DAYS", "504"))


_DEFAULT_ETF: dict[str, Any] = {
    "symbols": ["EXSA.DE", "VWCE.DE", "EUNL.DE", "IS3N.DE", "SXR8.DE", "AGGH.DE", "IBCI.DE"],
    "h0_calibration_days": _env_calibration_days(),
    "h0_sigma_floor": 0.0,
    "h0_band_sigma_mult": 2.0,
    "h0_seasonal_dow": True,
    "h0_mu_anchor_days": 252,
    "h0_macro_fair_scale": 1.0,
    "h0_realized_vol_sigma_frac": 0.0,
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
    "h0_sigma_floor": 0.015,
    "h0_band_sigma_mult": 2.5,
    "h0_seasonal_dow": False,
    "h0_mu_anchor_days": 126,
    "h0_macro_fair_scale": 0.5,
    "h0_realized_vol_sigma_frac": 0.75,
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
    "h0_sigma_floor": 0.01,
    "h0_band_sigma_mult": 2.0,
    "h0_seasonal_dow": True,
    "h0_mu_anchor_days": 189,
    "h0_macro_fair_scale": 1.0,
    "h0_realized_vol_sigma_frac": 0.5,
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


_DEFAULT_STRATEGY_ROUTER: dict[str, Any] = {
    "trend_z_min": 0.5,
    "range_z_max": 0.3,
    "ma_cross_lookback_days": 90,
    "ma_cross_max_for_range": 2,
    "regime_min_days": 10,
    "default_model": "perturbation",
}


_DEFAULT_MOMENTUM_BENCHMARK: dict[str, Any] = {
    "fast_ma_days": 50,
    "slow_ma_days": 200,
    "rsi_period": 14,
    "rsi_mode": "momentum",
    "rsi_buy_min": 50.0,
    "rsi_sell_max": 50.0,
    "rsi_oversold": 30.0,
    "rsi_overbought": 70.0,
    "momentum_lookback_days": 63,
    "momentum_threshold": 0.0,
    "require_momentum_for_buy": False,
    "exit_on_rsi_weak": True,
    "position_mode": "scale",
}


StrategyModelName = Literal["perturbation", "momentum_benchmark"]
MarketRegimeName = Literal["trending", "ranging", "uncertain"]


@dataclass(frozen=True)
class StrategyRouterConfig:
    """Rule-based regime router: trending → momentum, ranging → perturbation."""

    trend_z_min: float
    range_z_max: float
    ma_cross_lookback_days: int
    ma_cross_max_for_range: int
    regime_min_days: int
    default_model: StrategyModelName


@dataclass(frozen=True)
class MomentumBenchmarkConfig:
    """RSI benchmark (momentum or mean-reversion; MAs kept for charts / regime routing)."""

    fast_ma_days: int
    slow_ma_days: int
    rsi_period: int
    rsi_mode: RsiMode
    rsi_buy_min: float
    rsi_sell_max: float
    rsi_oversold: float
    rsi_overbought: float
    momentum_lookback_days: int
    momentum_threshold: float
    require_momentum_for_buy: bool
    exit_on_rsi_weak: bool
    position_mode: MomentumPositionMode

    @property
    def exit_on_ma_crossunder(self) -> bool:
        """Deprecated alias for exit_on_rsi_weak (older configs / call sites)."""
        return self.exit_on_rsi_weak


def _parse_momentum_position_mode(data: dict[str, Any]) -> MomentumPositionMode:
    if "position_mode" in data:
        mode = str(data["position_mode"]).strip().lower()
        if mode not in ("slice", "scale", "full"):
            raise ValueError(
                f"momentum_benchmark.position_mode must be slice, scale, or full (got {mode!r})"
            )
        return mode  # type: ignore[return-value]
    # Legacy: full_position boolean
    if bool(data.get("full_position", False)):
        return "full"
    return "scale"


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
    h0_sigma_floor: float
    h0_band_sigma_mult: float
    h0_seasonal_dow: bool
    h0_mu_anchor_days: int
    h0_macro_fair_scale: float
    h0_realized_vol_sigma_frac: float

    def perturbation_weights(self) -> tuple[float, float, float]:
        return (self.w_return, self.w_volume, self.w_rel_strength)


@dataclass(frozen=True)
class UniverseConfig:
    benchmark: str
    currency: str
    aliases: dict[str, str]
    strategy_router: StrategyRouterConfig
    momentum_benchmark: MomentumBenchmarkConfig
    etf: AssetClassConfig
    mutual_fund: AssetClassConfig
    share: AssetClassConfig
    config_path: Path | None = None
    universe_path: Path | None = None

    def watchlist(self) -> list[str]:
        return list(self.etf.symbols) + list(self.mutual_fund.symbols) + list(self.share.symbols)

    def symbols_for_classes(self, classes: Iterable[AssetClassName]) -> list[str]:
        """Symbols belonging to one or more asset classes (preserves config order)."""
        class_list = tuple(classes)
        if not class_list:
            return self.watchlist()
        out: list[str] = []
        for name in class_list:
            out.extend(getattr(self, name).symbols)
        return out

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


def _resolve_config_relative(path_str: str, config_file: Path) -> Path:
    """Resolve a path relative to the main config file (then cwd, then repo root)."""
    path = Path(path_str)
    if path.is_file():
        return path.resolve()
    if path.is_absolute():
        return path
    for base in (config_file.parent, Path.cwd(), repo_root()):
        candidate = (base / path_str).resolve()
        if candidate.is_file():
            return candidate
    return (config_file.parent / path_str).resolve()


def _merge_universe_file(payload: dict[str, Any], config_file: Path) -> tuple[dict[str, Any], Path | None]:
    """Load aliases and per-class symbols from a shared universe file when configured."""
    universe_ref = os.getenv("FUNTRADE_UNIVERSE") or payload.get("universe")
    if not universe_ref:
        return payload, None

    uni_path = _resolve_config_relative(str(universe_ref), config_file)
    if not uni_path.is_file():
        raise FileNotFoundError(
            f"Universe file not found: {uni_path} (referenced as {universe_ref!r} in {config_file})"
        )

    uni_payload = json.loads(uni_path.read_text(encoding="utf-8"))
    if not isinstance(uni_payload, dict):
        raise ValueError(f"{uni_path}: root must be a JSON object")

    merged = dict(payload)
    uni_aliases = _parse_aliases(uni_payload.get("aliases"))
    main_aliases = _parse_aliases(payload.get("aliases"))
    merged["aliases"] = {**uni_aliases, **main_aliases}

    for name in ASSET_CLASSES:
        main_block = dict(payload.get(name) or {})
        uni_block = uni_payload.get(name) if isinstance(uni_payload.get(name), dict) else {}
        if "symbols" in uni_block:
            main_block["symbols"] = uni_block["symbols"]
        merged[name] = main_block

    return merged, uni_path


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
        h0_sigma_floor=float(data.get("h0_sigma_floor", defaults.get("h0_sigma_floor", 0.0))),
        h0_band_sigma_mult=float(data.get("h0_band_sigma_mult", defaults.get("h0_band_sigma_mult", 2.0))),
        h0_seasonal_dow=bool(data.get("h0_seasonal_dow", defaults.get("h0_seasonal_dow", True))),
        h0_mu_anchor_days=int(data.get("h0_mu_anchor_days", defaults.get("h0_mu_anchor_days", 252))),
        h0_macro_fair_scale=float(data.get("h0_macro_fair_scale", defaults.get("h0_macro_fair_scale", 1.0))),
        h0_realized_vol_sigma_frac=float(
            data.get("h0_realized_vol_sigma_frac", defaults.get("h0_realized_vol_sigma_frac", 0.0))
        ),
    )


def _parse_strategy_router(raw: dict[str, Any] | None) -> StrategyRouterConfig:
    data = {**_DEFAULT_STRATEGY_ROUTER, **(raw or {})}
    default_model = str(data["default_model"]).strip().lower()
    if default_model not in ("perturbation", "momentum_benchmark"):
        raise ValueError(
            f"strategy_router.default_model must be perturbation or momentum_benchmark (got {default_model!r})"
        )
    return StrategyRouterConfig(
        trend_z_min=float(data["trend_z_min"]),
        range_z_max=float(data["range_z_max"]),
        ma_cross_lookback_days=int(data["ma_cross_lookback_days"]),
        ma_cross_max_for_range=int(data["ma_cross_max_for_range"]),
        regime_min_days=int(data["regime_min_days"]),
        default_model=default_model,  # type: ignore[arg-type]
    )


def _parse_rsi_mode(data: dict[str, Any]) -> RsiMode:
    mode = str(data.get("rsi_mode", "momentum")).strip().lower()
    if mode not in ("momentum", "mean_reversion"):
        raise ValueError(
            f"momentum_benchmark.rsi_mode must be momentum or mean_reversion (got {mode!r})"
        )
    return mode  # type: ignore[return-value]


def _parse_momentum_benchmark(raw: dict[str, Any] | None) -> MomentumBenchmarkConfig:
    data = {**_DEFAULT_MOMENTUM_BENCHMARK, **(raw or {})}
    if "exit_on_rsi_weak" in data:
        exit_weak = bool(data["exit_on_rsi_weak"])
    else:
        exit_weak = bool(data.get("exit_on_ma_crossunder", True))
    return MomentumBenchmarkConfig(
        fast_ma_days=int(data["fast_ma_days"]),
        slow_ma_days=int(data["slow_ma_days"]),
        rsi_period=max(2, int(data["rsi_period"])),
        rsi_mode=_parse_rsi_mode(data),
        rsi_buy_min=float(data["rsi_buy_min"]),
        rsi_sell_max=float(data["rsi_sell_max"]),
        rsi_oversold=float(data["rsi_oversold"]),
        rsi_overbought=float(data["rsi_overbought"]),
        momentum_lookback_days=int(data["momentum_lookback_days"]),
        momentum_threshold=float(data["momentum_threshold"]),
        require_momentum_for_buy=bool(data["require_momentum_for_buy"]),
        exit_on_rsi_weak=exit_weak,
        position_mode=_parse_momentum_position_mode(data),
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

    payload, universe_path = _merge_universe_file(payload, path)

    benchmark = str(payload.get("benchmark", "EXSA.DE"))
    currency = str(payload.get("currency", "EUR"))
    aliases = _parse_aliases(payload.get("aliases"))

    universe = UniverseConfig(
        benchmark=benchmark,
        currency=currency,
        aliases=aliases,
        strategy_router=_parse_strategy_router(payload.get("strategy_router")),
        momentum_benchmark=_parse_momentum_benchmark(payload.get("momentum_benchmark")),
        etf=_parse_asset_class("etf", payload.get("etf"), _DEFAULT_ETF),
        mutual_fund=_parse_asset_class("mutual_fund", payload.get("mutual_fund"), _DEFAULT_MUTUAL_FUND),
        share=_parse_asset_class("share", payload.get("share"), _DEFAULT_SHARE),
        config_path=path,
        universe_path=universe_path,
    )
    _cached = universe
    return universe


def reset_universe_config_cache() -> None:
    global _cached
    _cached = None
