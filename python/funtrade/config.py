"""Database connection and configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
from dotenv import load_dotenv


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

    @classmethod
    def from_env(cls) -> Settings:
        watchlist_raw = os.getenv(
            "WATCHLIST",
            "EXSA.DE,VWCE.DE,EUNL.DE,IS3N.DE,SXR8.DE,AGGH.DE,IBCI.DE",
        )
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql://funtrade:funtrade@localhost:5433/funtrade",
            ),
            watchlist=[s.strip() for s in watchlist_raw.split(",") if s.strip()],
            benchmark=os.getenv("BENCHMARK", "EXSA.DE"),
            currency=os.getenv("CURRENCY", "EUR"),
            epsilon_threshold=float(os.getenv("EPSILON_THRESHOLD", "0.5")),
            regime_spike_sigma=float(os.getenv("REGIME_SPIKE_SIGMA", "3.0")),
            regime_consecutive_bars=int(os.getenv("REGIME_CONSECUTIVE_BARS", "3")),
            min_daily_volume_eur=float(os.getenv("MIN_DAILY_VOLUME_EUR", "100000")),
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
            trend_use_benchmark=_env_bool("TREND_USE_BENCHMARK", False),
            trend_epsilon_weight=float(os.getenv("TREND_EPSILON_WEIGHT", "0.15")),
            trend_fair_value_weight=float(os.getenv("TREND_FAIR_VALUE_WEIGHT", "0.0")),
            trend_gate_sells=_env_bool("TREND_GATE_SELLS", True),
            trend_gate_z=float(os.getenv("TREND_GATE_Z", "0.5")),
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
