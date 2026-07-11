"""Traditional moving-average / momentum benchmark strategy (comparison baseline)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from funtrade.config import Settings
from funtrade.data.loader import MARKET_ADJ_CLOSE, load_price_bars
from funtrade.universe_config import MomentumBenchmarkConfig


@dataclass
class MomentumResult:
    time: pd.Timestamp
    symbol: str
    asset_class: str
    price: float
    fast_ma: float
    slow_ma: float
    momentum: float
    ma_bullish: bool


def _min_periods(window: int) -> int:
    return max(5, window // 4)


def compute_momentum_series(
    symbol: str,
    *,
    settings: Settings | None = None,
    config: MomentumBenchmarkConfig | None = None,
    max_bars: int | None = None,
) -> pd.DataFrame:
    """Daily MA crossover and momentum features for backtest and recommendations."""
    if settings is None:
        settings = Settings.from_env().for_symbol(symbol)
    if config is None:
        if settings.universe is None:
            raise ValueError("Momentum benchmark config requires universe (config.json)")
        config = settings.universe.momentum_benchmark

    df = load_price_bars(symbol, MARKET_ADJ_CLOSE, settings=settings)
    if df.empty:
        return pd.DataFrame()
    if max_bars is not None and len(df) > max_bars:
        df = df.tail(max_bars)

    price = df["price"].astype(float)
    fast = price.rolling(config.fast_ma_days, min_periods=_min_periods(config.fast_ma_days)).mean()
    slow = price.rolling(config.slow_ma_days, min_periods=_min_periods(config.slow_ma_days)).mean()
    mom = price / price.shift(config.momentum_lookback_days) - 1.0

    out = pd.DataFrame(
        {
            "price": price,
            "fast_ma": fast,
            "slow_ma": slow,
            "momentum": mom,
            "ma_bullish": fast > slow,
        },
        index=price.index,
    )
    return out.dropna(how="all")


def signal_from_momentum(
    *,
    fast_ma: float,
    slow_ma: float,
    momentum: float,
    current_position: float,
    config: MomentumBenchmarkConfig,
) -> int:
    """Return +1 (buy), -1 (sell/exit), or 0 (hold). Long-only MA/momentum rules."""
    if pd.isna(fast_ma) or pd.isna(slow_ma):
        return 0

    bullish = fast_ma > slow_ma
    if bullish:
        if config.require_momentum_for_buy and (
            pd.isna(momentum) or momentum < config.momentum_threshold
        ):
            return 0
        if current_position <= 0:
            return 1
        return 0

    if config.exit_on_ma_crossunder and current_position > 0:
        return -1
    return 0


def detect_latest_momentum(
    symbols: list[str] | None = None,
    *,
    settings: Settings | None = None,
) -> list[MomentumResult]:
    """Latest-bar momentum snapshot for each symbol (recommendations)."""
    settings = settings or Settings.from_env()
    if settings.universe is None:
        return []
    config = settings.universe.momentum_benchmark
    symbols = symbols or settings.watchlist
    results: list[MomentumResult] = []

    for symbol in symbols:
        try:
            sym_settings = settings.for_symbol(symbol)
            tail = max(config.slow_ma_days, config.momentum_lookback_days) + 60
            series = compute_momentum_series(
                symbol, settings=sym_settings, config=config, max_bars=tail,
            )
            if series.empty:
                continue
            latest = series.iloc[-1]
            ts = series.index[-1]
            results.append(
                MomentumResult(
                    time=ts,
                    symbol=symbol,
                    asset_class=sym_settings.asset_class or "etf",
                    price=float(latest["price"]),
                    fast_ma=float(latest["fast_ma"]),
                    slow_ma=float(latest["slow_ma"]),
                    momentum=float(latest["momentum"]) if not pd.isna(latest["momentum"]) else float("nan"),
                    ma_bullish=bool(latest["ma_bullish"]),
                )
            )
        except Exception:
            continue

    return results
