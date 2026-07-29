"""외부 서비스 클라이언트 — mock 모드 · 실패 폴백

07은 다른 서비스에 **읽기만** 한다. 상대가 죽어 있어도 리포트가 나와야 하므로
폴백 경로를 실패 주입으로 검증한다.
"""
from __future__ import annotations

import httpx
import pytest

from app.services.clients.response_client import ResponseClient
from app.services.clients.scheduler_client import (
    MOCK_RULE_COMPLIANCE,
    RULE_KEYS,
    SchedulerClient,
)

ROUND = "R2026-Q3-01"


class _FakeResponse:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


# --- SchedulerClient ---------------------------------------------------------


def test_scheduler_mock_returns_all_four_rules():
    rules = SchedulerClient().get_rule_compliance(ROUND)

    assert rules == MOCK_RULE_COMPLIANCE
    assert sorted(rules) == sorted(RULE_KEYS)


def test_scheduler_reads_live_response(monkeypatch):
    payload = {"data": {"rules": {"RULE1_GRAD_BALANCE": 91.0, "RULE2_TEAM_CONFLICT": "97"}}}
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(payload))

    rules = SchedulerClient(use_mock=False).get_rule_compliance(ROUND)

    assert rules == {"RULE1_GRAD_BALANCE": 91.0, "RULE2_TEAM_CONFLICT": 97.0}


def test_scheduler_accepts_flat_rules_payload(monkeypatch):
    """`data.rules`가 없으면 `data` 자체를 규칙 맵으로 읽는다"""
    monkeypatch.setattr(
        httpx, "get", lambda *a, **kw: _FakeResponse({"data": {"RULE4_FIRST_SLOT": 80}})
    )

    assert SchedulerClient(use_mock=False).get_rule_compliance(ROUND) == {
        "RULE4_FIRST_SLOT": 80.0
    }


def test_scheduler_skips_non_numeric_values(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **kw: _FakeResponse(
            {"data": {"rules": {"RULE1_GRAD_BALANCE": 88.0, "note": "n/a"}}}
        ),
    )

    assert SchedulerClient(use_mock=False).get_rule_compliance(ROUND) == {
        "RULE1_GRAD_BALANCE": 88.0
    }


def test_scheduler_falls_back_to_mock_when_service_is_down(monkeypatch):
    def explode(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", explode)

    assert SchedulerClient(use_mock=False).get_rule_compliance(ROUND) == MOCK_RULE_COMPLIANCE


def test_report_still_generated_when_scheduler_is_down(monkeypatch, seeded):
    """Service 04 장애가 리포트 생성을 막지 않는다"""
    from app.services.report_generator import ReportGenerator

    def explode(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", explode)

    report, _ = ReportGenerator(
        seeded, scheduler_client=SchedulerClient(use_mock=False)
    ).round_summary("R2026-Q3-01")

    assert report["rule_compliance"]["rules"] == MOCK_RULE_COMPLIANCE
    assert report["rule_compliance"]["overall"] == pytest.approx(90.5)


# --- ResponseClient ----------------------------------------------------------


def test_response_client_mock_returns_empty():
    assert ResponseClient().get_org_stats(ROUND) == []


def test_response_client_reads_live_stats(monkeypatch):
    payload = {"data": [{"org": "제1기술원", "mean_hours": 6.0, "completion_rate": 95.0}]}
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(payload))

    stats = ResponseClient(use_mock=False).get_org_stats(ROUND)

    assert stats[0]["org"] == "제1기술원"


def test_response_client_ignores_non_list_payload(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse({"data": {"x": 1}}))

    assert ResponseClient(use_mock=False).get_org_stats(ROUND) == []


def test_response_client_survives_outage(monkeypatch):
    def explode(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", explode)

    assert ResponseClient(use_mock=False).get_org_stats(ROUND) == []


def test_client_base_url_is_normalized():
    client = SchedulerClient(base_url="http://localhost:8004/")
    assert client.base_url == "http://localhost:8004"
