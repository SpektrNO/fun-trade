"""Tests for chart renderer factory and data prep."""

from __future__ import annotations

import pandas as pd
import pytest

from funtrade.ui.plotting.backends.plotly import PlotlyRenderer, _chart_key
from funtrade.ui.plotting.backends.streamlit_native import StreamlitNativeRenderer
from funtrade.ui.plotting.data import normalize_chart_times, prepare_trade_chart_frames, regime_invalid_spans
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


def test_prepare_trade_chart_frames_includes_regime_valid():
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    series = pd.DataFrame(
        {
            "epsilon": [0.0, -1.0, 0.2],
            "price": [50.0, 51.0, 52.0],
            "regime_valid": [True, False, True],
        },
        index=idx,
    )
    frames = prepare_trade_chart_frames(series, epsilon_threshold=0.75, trend_enable=False)
    assert "regime_valid" in frames["epsilon"].columns


def test_regime_invalid_spans():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    df = pd.DataFrame(
        {"time": idx, "regime_valid": [True, False, False, True, False]},
    )
    spans = regime_invalid_spans(df)
    assert len(spans) == 2
    assert spans[0] == (idx[1], idx[3])
    assert spans[1] == (idx[4], idx[4] + pd.Timedelta(days=1))


def test_normalize_chart_times_strips_tz():
    idx = pd.date_range("2024-01-01", periods=2, freq="D", tz="UTC")
    out = normalize_chart_times(pd.Series(idx))
    assert out.dt.tz is None
    assert str(out.iloc[0]) == "2024-01-01 00:00:00"


def test_chart_key_unique_when_long():
    long_base = "bt-" + "x" * 120
    k1 = _chart_key("ts", f"{long_base}-epsilon")
    k2 = _chart_key("ts", f"{long_base}-price")
    assert k1 != k2
    assert len(k1) <= 80
    assert len(k2) <= 80
