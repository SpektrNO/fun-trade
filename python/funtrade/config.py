"""Database connection and configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
from dotenv import load_dotenv

from funtrade.universe_config import AssetClassName, UniverseConfig, load_universe_config


def _load_env_file() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    for candidate in (Path.cwd() / ".env", repo_root / ".env", Path.cwd().parent / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return
    load_dotenv(override=False)


_load_env_file()


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    database_url: str
    watchlist: list[str]
    benchmark: str
    currency: str
    epsilon_threshold: float
    regime_spike_sigma: float
    regime_consecutive_bars: int
    min_daily_volume_eur: float
    w_return: float
    w_volume: float
    w_rel_strength: float
    h0_weight_eur_rates: float
    h0_weight_credit_spread: float
    h0_weight_eur_usd: float
    h0_weight_sector_beta: float
    h0_enable_oil: bool
    h0_oil_ticker: str
    h0_weight_oil: float
    h0_enable_climate: bool
    h0_climate_mode: str
    h0_climate_ticker: str
    h0_climate_clean_ticker: str
    h0_climate_fossil_ticker: str
    h0_weight_climate: float
    trend_enable: bool
    trend_lookback_days: int
    trend_use_benchmark: bool
    trend_epsilon_weight: float
    trend_fair_value_weight: float
    trend_gate_sells: bool
    trend_gate_z: float
    universe: UniverseConfig | None = None
    asset_class: AssetClassName | None = None
    h0_calibration_days: int = 504
    h0_sigma_floor: float = 0.0
    h0_band_sigma_mult: float = 2.0
    h0_seasonal_dow: bool = True
    h0_mu_anchor_days: int = 252
    h0_macro_fair_scale: float = 1.0
    h0_realized_vol_sigma_frac: float = 0.0
    chart_backend: str = "streamlit"

    @classmethod
    def from_env(cls) -> Settings:
        universe = load_universe_config()
        etf = universe.etf
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql://funtrade:funtrade@localhost:5433/funtrade",
            ),
            watchlist=universe.watchlist(),
            benchmark=universe.benchmark,
            currency=universe.currency,
            epsilon_threshold=etf.epsilon_threshold,
            regime_spike_sigma=etf.regime_spike_sigma,
            regime_consecutive_bars=etf.regime_consecutive_bars,
            min_daily_volume_eur=etf.min_daily_volume_eur,
            w_return=etf.w_return,
            w_volume=etf.w_volume,
            w_rel_strength=etf.w_rel_strength,
            h0_weight_eur_rates=float(os.getenv("H0_WEIGHT_EUR_RATES", "0.15")),
            h0_weight_credit_spread=float(os.getenv("H0_WEIGHT_CREDIT_SPREAD", "0.10")),
            h0_weight_eur_usd=float(os.getenv("H0_WEIGHT_EUR_USD", "0.10")),
            h0_weight_sector_beta=float(os.getenv("H0_WEIGHT_SECTOR_BETA", "-0.10")),
            h0_enable_oil=_env_bool("H0_ENABLE_OIL", False),
            h0_oil_ticker=os.getenv("H0_OIL_TICKER", "BZ=F"),
            h0_weight_oil=float(os.getenv("H0_WEIGHT_OIL", "-0.08")),
            h0_enable_climate=_env_bool("H0_ENABLE_CLIMATE", False),
            h0_climate_mode=os.getenv("H0_CLIMATE_MODE", "spread").strip().lower(),
            h0_climate_ticker=os.getenv("H0_CLIMATE_TICKER", "INRG.L"),
            h0_climate_clean_ticker=os.getenv("H0_CLIMATE_CLEAN_TICKER", "INRG.L"),
            h0_climate_fossil_ticker=os.getenv("H0_CLIMATE_FOSSIL_TICKER", "BZ=F"),
            h0_weight_climate=float(os.getenv("H0_WEIGHT_CLIMATE", "0.06")),
            trend_enable=_env_bool("TREND_ENABLE", False),
            trend_lookback_days=int(os.getenv("TREND_LOOKBACK_DAYS", "200")),
            trend_use_benchmark=etf.trend_use_benchmark,
            trend_epsilon_weight=etf.trend_epsilon_weight,
            trend_fair_value_weight=etf.trend_fair_value_weight,
            trend_gate_sells=etf.trend_gate_sells,
            trend_gate_z=etf.trend_gate_z,
            universe=universe,
            asset_class="etf",
            h0_calibration_days=etf.h0_calibration_days,
            h0_sigma_floor=etf.h0_sigma_floor,
            h0_band_sigma_mult=etf.h0_band_sigma_mult,
            h0_seasonal_dow=etf.h0_seasonal_dow,
            h0_mu_anchor_days=etf.h0_mu_anchor_days,
            h0_macro_fair_scale=etf.h0_macro_fair_scale,
            h0_realized_vol_sigma_frac=etf.h0_realized_vol_sigma_frac,
            chart_backend=os.getenv("FUNTRADE_CHART_BACKEND", "streamlit").strip().lower(),
        )

    def perturbation_weights(self) -> tuple[float, float, float]:
        return (self.w_return, self.w_volume, self.w_rel_strength)

    def for_symbol(self, symbol: str) -> Settings:
        """Apply asset-class trading params from config.json for this symbol."""
        if self.universe is None:
            return self
        cls_cfg = self.universe.for_symbol(symbol)
        asset_class = self.universe.class_of(symbol)
        return replace(
            self,
            asset_class=asset_class,
            epsilon_threshold=cls_cfg.epsilon_threshold,
            regime_spike_sigma=cls_cfg.regime_spike_sigma,
            regime_consecutive_bars=cls_cfg.regime_consecutive_bars,
            min_daily_volume_eur=cls_cfg.min_daily_volume_eur,
            w_return=cls_cfg.w_return,
            w_volume=cls_cfg.w_volume,
            w_rel_strength=cls_cfg.w_rel_strength,
            trend_use_benchmark=cls_cfg.trend_use_benchmark,
            trend_epsilon_weight=cls_cfg.trend_epsilon_weight,
            trend_fair_value_weight=cls_cfg.trend_fair_value_weight,
            trend_gate_sells=cls_cfg.trend_gate_sells,
            trend_gate_z=cls_cfg.trend_gate_z,
            h0_calibration_days=cls_cfg.h0_calibration_days,
            h0_sigma_floor=cls_cfg.h0_sigma_floor,
            h0_band_sigma_mult=cls_cfg.h0_band_sigma_mult,
            h0_seasonal_dow=cls_cfg.h0_seasonal_dow,
            h0_mu_anchor_days=cls_cfg.h0_mu_anchor_days,
            h0_macro_fair_scale=cls_cfg.h0_macro_fair_scale,
            h0_realized_vol_sigma_frac=cls_cfg.h0_realized_vol_sigma_frac,
        )

    def active_h0_component_ids(self) -> tuple[str, ...]:
        from funtrade.models.components import CORE_H0_COMPONENT_IDS

        ids = list(CORE_H0_COMPONENT_IDS)
        if self.h0_enable_oil:
            ids.append("oil_price")
        if self.h0_enable_climate:
            ids.append("climate_transition")
        return tuple(ids)

    def h0_weights(self) -> dict[str, float]:
        weights = {
            "eur_rates": self.h0_weight_eur_rates,
            "credit_spread": self.h0_weight_credit_spread,
            "eur_usd": self.h0_weight_eur_usd,
            "sector_beta": self.h0_weight_sector_beta,
        }
        if self.h0_enable_oil:
            weights["oil_price"] = self.h0_weight_oil
        if self.h0_enable_climate:
            weights["climate_transition"] = self.h0_weight_climate
        return weights


def normalize_dsn(url: str) -> str:
    if url.startswith("Host="):
        parts = {}
        for segment in url.split(";"):
            if "=" not in segment:
                continue
            key, value = segment.split("=", 1)
            parts[key.strip()] = value.strip()
        user = parts.get("Username", parts.get("User ID", "funtrade"))
        password = parts.get("Password", "funtrade")
        host = parts.get("Host", "localhost")
        port = parts.get("Port", "5433")
        database = parts.get("Database", "funtrade")
        return f"postgresql://{user}:{password}@{host}:{port}/{database}"
    return url


def get_connection(settings: Settings | None = None) -> psycopg.Connection:
    settings = settings or Settings.from_env()
    return psycopg.connect(normalize_dsn(settings.database_url))


def read_sql_df(
    query: str,
    params: dict[str, Any] | None = None,
    *,
    settings: Settings | None = None,
) -> pd.DataFrame:
    with get_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or {})
            if cur.description is None:
                return pd.DataFrame()
            columns = [col.name for col in cur.description]
            rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)
