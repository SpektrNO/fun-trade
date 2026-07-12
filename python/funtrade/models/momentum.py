"""Traditional moving-average / momentum benchmark strategy (comparison baseline)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from funtrade.config import Settings
from funtrade.data.loader import MARKET_ADJ_CLOSE, load_price_bars, load_price_bars_batch
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


def momentum_frame_from_prices(price: pd.Series, config: MomentumBenchmarkConfig) -> pd.DataFrame:
    """MA/momentum features from a price series (used by backtest and batch recommendations)."""
    fast = price.rolling(config.fast_ma_days, min_periods=_min_periods(config.fast_ma_days)).mean()
    slow = price.rolling(config.slow_ma_days, min_periods=_min_periods(config.slow_ma_days)).mean()
    mom = price / price.shift(config.momentum_lookback_days) - 1.0
    return pd.DataFrame(
        {
            "price": price,
            "fast_ma": fast,
            "slow_ma": slow,
            "momentum": mom,
            "ma_bullish": fast > slow,
        },
        index=price.index,
    ).dropna(how="all")


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
    return momentum_frame_from_prices(price, config)


def _snapshot_from_series(
    series: pd.DataFrame,
    *,
    symbol: str,
    asset_class: str,
) -> MomentumResult | None:
    if series.empty:
        return None
    latest = series.iloc[-1]
    if pd.isna(latest.get("fast_ma")) or pd.isna(latest.get("slow_ma")):
        return None
    ts = series.index[-1]
    return MomentumResult(
        time=ts,
        symbol=symbol,
        asset_class=asset_class,
        price=float(latest["price"]),
        fast_ma=float(latest["fast_ma"]),
        slow_ma=float(latest["slow_ma"]),
        momentum=float(latest["momentum"]) if not pd.isna(latest["momentum"]) else float("nan"),
        ma_bullish=bool(latest["ma_bullish"]),
    )


def momentum_regime_bullish(
    *,
    fast_ma: float,
    slow_ma: float,
    momentum: float,
    config: MomentumBenchmarkConfig,
) -> bool:
    if pd.isna(fast_ma) or pd.isna(slow_ma) or fast_ma <= slow_ma:
        return False
    if config.require_momentum_for_buy and (
        pd.isna(momentum) or momentum < config.momentum_threshold
    ):
        return False
    return True


def momentum_backtest_signal(
    *,
    fast_ma: float,
    slow_ma: float,
    momentum: float,
    current_position: float,
    config: MomentumBenchmarkConfig,
) -> int:
    """Daily trade intent for momentum backtest (+1 buy slice, -1 sell slice, 0 hold)."""
    if config.position_mode == "scale":
        if momentum_regime_bullish(
            fast_ma=fast_ma, slow_ma=slow_ma, momentum=momentum, config=config,
        ):
            return 1
        if (
            config.exit_on_ma_crossunder
            and current_position > 0
            and not pd.isna(fast_ma)
            and not pd.isna(slow_ma)
            and fast_ma <= slow_ma
        ):
            return -1
        return 0
    return signal_from_momentum(
        fast_ma=fast_ma,
        slow_ma=slow_ma,
        momentum=momentum,
        current_position=current_position,
        config=config,
    )


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


def momentum_trade_qty(
    *,
    side: str,
    price: float,
    cash_eur: float,
    net_qty: float,
    paper,
    config: MomentumBenchmarkConfig,
    qty_shares: float | None = None,
) -> float:
    """Share qty for momentum backtest by position_mode (slice / scale / full)."""
    from funtrade.execution.paper import MIN_TRADE_EUR, _fee_rate, compute_trade_qty

    if qty_shares is not None:
        qty = qty_shares
        if side == "sell":
            qty = min(qty, net_qty)
        return max(qty, 0.0)

    if config.position_mode == "full":
        if price <= 0:
            return 0.0
        if side == "sell":
            return max(net_qty, 0.0)
        fee_mult = 1.0 + _fee_rate(paper.fee_bps)
        room = paper.position_limit_shares - net_qty
        if room <= 0 or cash_eur <= 0:
            return 0.0
        max_qty = min(room, cash_eur / (price * fee_mult))
        if max_qty * price < MIN_TRADE_EUR:
            return 0.0
        return max(max_qty, 0.0)

    if config.position_mode == "scale":
        return compute_trade_qty(
            side=side,
            price=price,
            cash_eur=cash_eur,
            net_qty=net_qty,
            paper=paper,
        )

    # slice: one paper slice on entry, full exit on crossunder sell signal
    if side == "sell":
        return max(net_qty, 0.0)
    return compute_trade_qty(
        side=side,
        price=price,
        cash_eur=cash_eur,
        net_qty=net_qty,
        paper=paper,
    )


def detect_latest_momentum(
    symbols: list[str] | None = None,
    *,
    settings: Settings | None = None,
) -> list[MomentumResult]:
    """Latest-bar momentum snapshot for each symbol (one batched price query)."""
    settings = settings or Settings.from_env()
    if settings.universe is None:
        return []
    config = settings.universe.momentum_benchmark
    symbols = symbols or settings.watchlist
    if not symbols:
        return []

    tail = max(config.slow_ma_days, config.momentum_lookback_days) + 60
    bars_by_symbol = load_price_bars_batch(symbols, tail_bars=tail, settings=settings)
    results: list[MomentumResult] = []

    for symbol in symbols:
        try:
            sym_settings = settings.for_symbol(symbol)
            df = bars_by_symbol.get(symbol)
            if df is None or df.empty:
                continue
            series = momentum_frame_from_prices(df["price"].astype(float), config)
            snap = _snapshot_from_series(
                series,
                symbol=symbol,
                asset_class=sym_settings.asset_class or "etf",
            )
            if snap is not None:
                results.append(snap)
        except Exception:
            continue

    return results
