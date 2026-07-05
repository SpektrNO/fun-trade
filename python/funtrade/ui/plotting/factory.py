"""Chart renderer factory."""

from __future__ import annotations

from funtrade.config import Settings
from funtrade.ui.plotting.backends.plotly import PlotlyRenderer
from funtrade.ui.plotting.backends.streamlit_native import StreamlitNativeRenderer
from funtrade.ui.plotting.base import ChartRenderer

_REGISTRY: dict[str, type[ChartRenderer]] = {
    "streamlit": StreamlitNativeRenderer,
    "plotly": PlotlyRenderer,
}


def get_chart_renderer(
    backend: str | None = None,
    settings: Settings | None = None,
) -> ChartRenderer:
    key = (backend or (settings or Settings.from_env()).chart_backend).lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        raise ValueError(f"Unknown chart backend {key!r}; choices: {sorted(_REGISTRY)}")
    return cls()
