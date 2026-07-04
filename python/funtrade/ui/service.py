"""Helpers for the Streamlit trading console."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from funtrade.backtest.engine import run_backtest
from funtrade.config import Settings
from funtrade.data.loader import load_latest_equilibrium_params
from funtrade.execution.paper import PaperSettings
from funtrade.models.perturbation import compute_perturbation_series, detect_latest_perturbations, signal_from_epsilon


@dataclass
class UiParams:
    symbol: str
    epsilon_threshold: float
    regime_spike_sigma: float
    regime_consecutive_bars: int
    w_return: float
    w_volume: float
    w_rel_strength: float
    paper_initial_cash: float
    paper_trade_shares: float
    paper_fee_bps: float
    paper_position_limit_shares: float

    def to_settings(self) -> Settings:
        base = Settings.from_env()
        return Settings(
            database_url=base.database_url,
            watchlist=base.watchlist,
            benchmark=base.benchmark,
            currency=base.currency,
            epsilon_threshold=self.epsilon_threshold,
            regime_spike_sigma=self.regime_spike_sigma,
            regime_consecutive_bars=self.regime_consecutive_bars,
            min_daily_volume_eur=base.min_daily_volume_eur,
        )

    def to_paper_settings(self) -> PaperSettings:
        base = PaperSettings.from_env()
        return PaperSettings(
            initial_cash=self.paper_initial_cash,
            position_limit_shares=self.paper_position_limit_shares,
            fee_bps=self.paper_fee_bps,
            trade_shares=self.paper_trade_shares,
            csv_path=base.csv_path,
        )

    def perturbation_weights(self) -> tuple[float, float, float]:
        return (self.w_return, self.w_volume, self.w_rel_strength)


def default_ui_params(symbol: str = "VWCE.DE") -> UiParams:
    s = Settings.from_env()
    p = PaperSettings.from_env()
    return UiParams(
        symbol=symbol,
        epsilon_threshold=s.epsilon_threshold,
        regime_spike_sigma=s.regime_spike_sigma,
        regime_consecutive_bars=s.regime_consecutive_bars,
        w_return=0.35,
        w_volume=0.10,
        w_rel_strength=0.25,
        paper_initial_cash=p.initial_cash,
        paper_trade_shares=p.trade_shares,
        paper_fee_bps=p.fee_bps,
        paper_position_limit_shares=p.position_limit_shares,
    )


def equilibrium_status(symbol: str, *, settings: Settings | None = None) -> dict | None:
    params = load_latest_equilibrium_params(symbol, settings=settings)
    if params is None:
        return None
    return {
        "kappa": params["kappa"],
        "mu": params["mu"],
        "sigma": params["sigma"],
        "half_life_days": params["half_life_days"],
        "calibrated_at": str(params["calibrated_at"]),
    }


def perturbation_context(
    symbol: str,
    *,
    weights: tuple[float, float, float] = (0.35, 0.10, 0.25),
    settings: Settings | None = None,
) -> pd.DataFrame:
    settings = settings or Settings.from_env()
    return compute_perturbation_series(symbol, weights=weights, settings=settings)


def run_backtest_for_ui(params: UiParams) -> dict:
    settings = params.to_settings()
    result = run_backtest(
        params.symbol,
        epsilon_threshold=params.epsilon_threshold,
        weights=params.perturbation_weights(),
        settings=settings,
        persist=False,
    )
    return {
        "sharpe": result.sharpe,
        "max_drawdown": result.max_drawdown,
        "total_return": result.total_return,
        "total_trades": result.total_trades,
        "regime_invalidations": result.regime_invalidations,
        "equity_curve": pd.DataFrame({"time": result.equity_curve.index, "equity": result.equity_curve.values}),
        "epsilon": pd.DataFrame({"time": result.epsilon.index, "epsilon": result.epsilon.values}),
    }
