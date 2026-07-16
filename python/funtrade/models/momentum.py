"""RSI momentum benchmark strategy (comparison baseline vs perturbation)."""

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
    rsi: float
    momentum: float
    rsi_bullish: bool
    ma_bullish: bool


def _min_periods(window: int) -> int:
    return max(5, window // 4)


def compute_rsi(price: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI (0–100). Uses EWM with alpha=1/period (standard approximation)."""
    period = max(2, int(period))
    delta = price.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # All gains / no losses → RSI 100; flat / no gains → RSI 0
    rsi = rsi.mask(avg_loss.eq(0.0) & avg_gain.gt(0.0), 100.0)
    rsi = rsi.mask(avg_gain.eq(0.0) & avg_loss.eq(0.0), 50.0)
    rsi = rsi.mask(avg_gain.eq(0.0) & avg_loss.gt(0.0), 0.0)
    return rsi.astype(float)


def momentum_frame_from_prices(price: pd.Series, config: MomentumBenchmarkConfig) -> pd.DataFrame:
    """RSI/MA features from a price series (used by backtest and batch recommendations)."""
    fast = price.rolling(config.fast_ma_days, min_periods=_min_periods(config.fast_ma_days)).mean()
    slow = price.rolling(config.slow_ma_days, min_periods=_min_periods(config.slow_ma_days)).mean()
    rsi = compute_rsi(price, config.rsi_period)
    mom = price / price.shift(config.momentum_lookback_days) - 1.0
    if config.rsi_mode == "mean_reversion":
        rsi_bullish = rsi < config.rsi_oversold
        rsi_oversold = rsi_bullish
        rsi_overbought = rsi > config.rsi_overbought
    else:
        rsi_bullish = rsi >= config.rsi_buy_min
        rsi_oversold = rsi < config.rsi_oversold
        rsi_overbought = rsi > config.rsi_overbought
    return pd.DataFrame(
        {
            "price": price,
            "fast_ma": fast,
            "slow_ma": slow,
            "rsi": rsi,
            "momentum": mom,
            "rsi_bullish": rsi_bullish,
            "rsi_oversold": rsi_oversold,
            "rsi_overbought": rsi_overbought,
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
    """Daily RSI momentum features for backtest and recommendations."""
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
    if pd.isna(latest.get("rsi")):
        return None
    ts = series.index[-1]
    return MomentumResult(
        time=ts,
        symbol=symbol,
        asset_class=asset_class,
        price=float(latest["price"]),
        fast_ma=float(latest["fast_ma"]) if not pd.isna(latest.get("fast_ma")) else float("nan"),
        slow_ma=float(latest["slow_ma"]) if not pd.isna(latest.get("slow_ma")) else float("nan"),
        rsi=float(latest["rsi"]),
        momentum=float(latest["momentum"]) if not pd.isna(latest["momentum"]) else float("nan"),
        rsi_bullish=bool(latest["rsi_bullish"]),
        ma_bullish=bool(latest["ma_bullish"]) if not pd.isna(latest.get("ma_bullish")) else False,
    )


def momentum_regime_bullish(
    *,
    rsi: float,
    momentum: float,
    config: MomentumBenchmarkConfig,
) -> bool:
    if config.rsi_mode == "mean_reversion":
        if pd.isna(rsi) or rsi >= config.rsi_oversold:
            return False
    elif pd.isna(rsi) or rsi < config.rsi_buy_min:
        return False
    if config.require_momentum_for_buy and (
        pd.isna(momentum) or momentum < config.momentum_threshold
    ):
        return False
    return True


def _mean_reversion_scale_signal(
    *,
    rsi: float,
    current_position: float,
    config: MomentumBenchmarkConfig,
) -> int:
    if pd.isna(rsi):
        return 0
    if rsi < config.rsi_oversold:
        return 1
    if current_position > 0 and rsi > config.rsi_overbought:
        return -1
    return 0


def _mean_reversion_slice_signal(
    *,
    rsi: float,
    current_position: float,
    config: MomentumBenchmarkConfig,
) -> int:
    if pd.isna(rsi):
        return 0
    if rsi < config.rsi_oversold:
        if current_position <= 0:
            return 1
        return 0
    if current_position > 0 and rsi > config.rsi_overbought:
        return -1
    return 0


def momentum_backtest_signal(
    *,
    rsi: float,
    momentum: float,
    current_position: float,
    config: MomentumBenchmarkConfig,
) -> int:
    """Daily trade intent for momentum backtest (+1 buy slice, -1 sell slice, 0 hold)."""
    if config.rsi_mode == "mean_reversion":
        if config.position_mode == "scale":
            return _mean_reversion_scale_signal(
                rsi=rsi, current_position=current_position, config=config,
            )
        return _mean_reversion_slice_signal(
            rsi=rsi, current_position=current_position, config=config,
        )
    if config.position_mode == "scale":
        if momentum_regime_bullish(rsi=rsi, momentum=momentum, config=config):
            return 1
        if (
            config.exit_on_rsi_weak
            and current_position > 0
            and not pd.isna(rsi)
            and rsi < config.rsi_sell_max
        ):
            return -1
        return 0
    return signal_from_momentum(
        rsi=rsi,
        momentum=momentum,
        current_position=current_position,
        config=config,
    )


def signal_from_momentum(
    *,
    rsi: float,
    momentum: float,
    current_position: float,
    config: MomentumBenchmarkConfig,
) -> int:
    """Return +1 (buy), -1 (sell/exit), or 0 (hold). Long-only RSI rules."""
    if pd.isna(rsi):
        return 0

    if config.rsi_mode == "mean_reversion":
        return _mean_reversion_slice_signal(
            rsi=rsi, current_position=current_position, config=config,
        )

    bullish = rsi >= config.rsi_buy_min
    if bullish:
        if config.require_momentum_for_buy and (
            pd.isna(momentum) or momentum < config.momentum_threshold
        ):
            return 0
        if current_position <= 0:
            return 1
        return 0

    if config.exit_on_rsi_weak and current_position > 0 and rsi < config.rsi_sell_max:
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

    # slice: one paper slice on entry, full exit on weak-RSI sell signal
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

    tail = max(config.slow_ma_days, config.momentum_lookback_days, config.rsi_period * 3) + 60
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
