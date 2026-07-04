import os

import pandas as pd

from funtrade.config import Settings
from funtrade.data.factors import compute_h0_fundamental_adjustment


def _settings(**overrides) -> Settings:
    base = Settings.from_env()
    return Settings(**{**base.__dict__, **overrides})


def test_h0_weights_exclude_optional_by_default(monkeypatch):
    monkeypatch.delenv("H0_ENABLE_OIL", raising=False)
    monkeypatch.delenv("H0_ENABLE_CLIMATE", raising=False)
    settings = Settings.from_env()
    weights = settings.h0_weights()
    assert "oil_price" not in weights
    assert "climate_transition" not in weights
    assert settings.active_h0_component_ids() == (
        "eur_rates",
        "credit_spread",
        "eur_usd",
        "sector_beta",
    )


def test_h0_weights_include_optional_when_enabled(monkeypatch):
    monkeypatch.setenv("H0_ENABLE_OIL", "true")
    monkeypatch.setenv("H0_ENABLE_CLIMATE", "yes")
    settings = Settings.from_env()
    weights = settings.h0_weights()
    assert weights["oil_price"] == settings.h0_weight_oil
    assert weights["climate_transition"] == settings.h0_weight_climate
    assert "oil_price" in settings.active_h0_component_ids()
    assert "climate_transition" in settings.active_h0_component_ids()


def test_compute_h0_adjustment_ignores_disabled_optional(monkeypatch):
    monkeypatch.setenv("H0_ENABLE_OIL", "false")
    index = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_read_sql_df(query, params, settings=None):
        component = params["component"]
        if component == "oil_price":
            return pd.DataFrame({"time": index, "value": [100.0] * len(index)})
        return pd.DataFrame(columns=["time", "value"])

    monkeypatch.setattr("funtrade.data.factors.read_sql_df", fake_read_sql_df)
    monkeypatch.setattr("funtrade.data.factors.get_connection", lambda settings=None: FakeConn())

    adj = compute_h0_fundamental_adjustment("VWCE.DE", index, settings=_settings(h0_enable_oil=False))
    assert (adj == 0.0).all()
