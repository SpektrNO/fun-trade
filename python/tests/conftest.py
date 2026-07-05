"""Pytest configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from funtrade.universe_config import reset_universe_config_cache

_FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "config.json"


@pytest.fixture(autouse=True)
def funtrade_test_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FUNTRADE_CONFIG", str(_FIXTURE_CONFIG))
    reset_universe_config_cache()
    yield
    reset_universe_config_cache()
