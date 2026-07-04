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
            epsilon_threshold=float(os.getenv("EPSILON_THRESHOLD", "2.0")),
            regime_spike_sigma=float(os.getenv("REGIME_SPIKE_SIGMA", "3.0")),
            regime_consecutive_bars=int(os.getenv("REGIME_CONSECUTIVE_BARS", "3")),
            min_daily_volume_eur=float(os.getenv("MIN_DAILY_VOLUME_EUR", "100000")),
        )


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
