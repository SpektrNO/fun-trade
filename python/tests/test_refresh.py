from funtrade.ui import service as svc


def test_run_refresh_runs_ingest_factors_and_detect(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        svc,
        "ingest_watchlist",
        lambda **kw: calls.append("ingest") or {"VWCE.DE": 14},
    )
    monkeypatch.setattr(
        svc,
        "ingest_macro_factors",
        lambda **kw: calls.append("factors") or {"eur_usd": 14},
    )
    monkeypatch.setattr(
        svc,
        "detect_latest_perturbations",
        lambda **kw: calls.append("detect") or [_FakeDetection()],
    )

    result = svc.run_refresh(days=14)

    assert calls == ["ingest", "factors", "detect"]
    assert result["ok"] is True
    assert result["steps"]["ingest"]["total_rows"] == 14
    assert result["steps"]["detect"]["symbols"] == 1
    assert "paper" not in result["steps"]


def test_run_refresh_stops_on_ingest_failure(monkeypatch):
    monkeypatch.setattr(
        svc,
        "ingest_watchlist",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("network down")),
    )
    monkeypatch.setattr(svc, "ingest_macro_factors", lambda **kw: {})
    monkeypatch.setattr(svc, "detect_latest_perturbations", lambda **kw: [])

    result = svc.run_refresh(days=7)

    assert result["ok"] is False
    assert "network down" in result["steps"]["ingest"]["error"]
    assert "detect" not in result["steps"]


class _FakeDetection:
    symbol = "VWCE.DE"
    epsilon = 0.1
    regime_valid = True
