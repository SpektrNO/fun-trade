import json

import pytest

from funtrade.portfolio.paper_sync import (
    build_paper_wallet_portfolio_payload,
    sync_portfolio_json_from_paper_wallet,
)


def test_build_paper_wallet_portfolio_payload_marks_to_market():
    summary = {
        "cash_eur": 42_500.5,
        "updated_at": "2026-07-21T08:00:00+00:00",
        "positions": [
            {
                "symbol": "VWCE.DE",
                "net_qty_shares": 10.0,
                "avg_price": 150.0,
                "mark_price": 164.4,
            },
            {
                "symbol": "AGGH.DE",
                "net_qty_shares": 5.0,
                "avg_price": 4.0,
                "mark_price": None,
            },
        ],
    }
    payload = build_paper_wallet_portfolio_payload(summary)
    assert payload["source"] == "paper_wallet"
    assert payload["cash_eur"] == pytest.approx(42_500.5)
    assert len(payload["holdings"]) == 2
    vwce = next(h for h in payload["holdings"] if h["symbol"] == "VWCE.DE")
    aggh = next(h for h in payload["holdings"] if h["symbol"] == "AGGH.DE")
    assert vwce["shares"] == pytest.approx(10.0)
    assert vwce["value_eur"] == pytest.approx(1644.0)
    assert aggh["value_eur"] == pytest.approx(20.0)


def test_sync_portfolio_json_from_paper_wallet_writes_file(monkeypatch, tmp_path):
    import funtrade.portfolio.paper_sync as sync

    monkeypatch.setattr(sync, "portfolio_sync_enabled", lambda: True)
    monkeypatch.setattr(
        sync,
        "get_portfolio_summary",
        lambda **kwargs: {
            "cash_eur": 100_000.0,
            "updated_at": "2026-07-21T08:00:00+00:00",
            "positions": [],
        },
    )

    target = tmp_path / "portfolio.json"
    written = sync_portfolio_json_from_paper_wallet(path=target)
    assert written == target.resolve()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["source"] == "paper_wallet"
    assert payload["holdings"] == []
    assert payload["cash_eur"] == pytest.approx(100_000.0)


def test_sync_portfolio_json_skipped_when_disabled(monkeypatch, tmp_path):
    import funtrade.portfolio.paper_sync as sync

    monkeypatch.setattr(sync, "portfolio_sync_enabled", lambda: False)
    target = tmp_path / "portfolio.json"
    assert sync_portfolio_json_from_paper_wallet(path=target) is None
    assert not target.exists()
