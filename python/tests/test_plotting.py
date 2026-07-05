"""Tests for chart renderer factory and data prep."""

from __future__ import annotations

import pandas as pd
import pytest

from funtrade.ui.plotting.backends.plotly import PlotlyRenderer
from funtrade.ui.plotting.backends.streamlit_native import StreamlitNativeRenderer
from funtrade.ui.plotting.data import prepare_trade_chart_frames
from funtrade.ui.plotting.factory import get_chart_renderer


def test_get_chart_renderer_streamlit():
    renderer = get_chart_renderer(backend="streamlit")
    assert isinstance(renderer, StreamlitNativeRenderer)


def test_get_chart_renderer_plotly():
    renderer = get_chart_renderer(backend="plotly")
    assert isinstance(renderer, PlotlyRenderer)


def test_get_chart_renderer_unknown_raises():
    with pytest.raises(ValueError, match="Unknown chart backend"):
        get_chart_renderer(backend="altair")


def test_prepare_trade_chart_frames():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    series = pd.DataFrame(
        {"epsilon": [0.1, 0.2, -0.1, 0.5, 0.3], "price": [100.0] * 5, "z_trend": [0.2] * 5},
        index=idx,
    )
    frames = prepare_trade_chart_frames(series, epsilon_threshold=0.75, trend_enable=True, trend_gate_z=0.5)

    assert set(frames) == {"epsilon", "price", "z_trend"}
    assert list(frames["epsilon"].columns) == ["time", "epsilon", "upper", "lower", "zero"]
    assert frames["epsilon"]["upper"].iloc[0] == 0.75
    assert frames["epsilon"]["lower"].iloc[0] == -0.75
    assert "gate" in frames["z_trend"].columns
    assert frames["z_trend"]["gate"].iloc[0] == 0.5


def test_prepare_trade_chart_frames_no_trend():
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    series = pd.DataFrame({"epsilon": [0.0, 0.1, 0.2], "price": [50.0, 51.0, 52.0]}, index=idx)
    frames = prepare_trade_chart_frames(series, epsilon_threshold=1.0, trend_enable=False)
    assert "z_trend" not in frames
