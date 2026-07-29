"""명세 §완료 판정 체크리스트 — 5개 항목을 그대로 검증

    - 18종 이벤트 모두 수신·저장 확인
    - /dashboard/kpi 응답 500ms 이내
    - /reports/rounds/{id} 10초 이내
    - Before/After 리포트에서 검증된 수치 재현 (회신 -60%, 규칙 준수 +25pp)
    - 위험 신호 4종 감지 로직 동작
"""
from __future__ import annotations

import time

import pytest

from app.domain.kpi import Metric
from app.events import ALL_EVENT_TYPES
from app.services.demo_data import BASELINE_ROUND, DEMO_ROUND
from app.services.risk_detector import RISK_RULES, detect_risks


def test_checklist_1_all_18_event_types_collected(seeded_client):
    stats = seeded_client.get("/api/v1/audit/events/stats").json()["data"]

    assert stats["catalog_size"] == 18
    assert stats["missing_types"] == []
    assert set(stats["covered_types"]) == set(ALL_EVENT_TYPES)


def test_checklist_2_dashboard_kpi_under_500ms(seeded_client):
    # 첫 호출로 warm-up 후 측정 (콜드 스타트 import 비용 제외)
    seeded_client.get(f"/api/v1/dashboard/kpi?round_id={DEMO_ROUND}")

    started = time.perf_counter()
    response = seeded_client.get(f"/api/v1/dashboard/kpi?round_id={DEMO_ROUND}")
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert response.status_code == 200
    assert elapsed_ms < 500, f"/dashboard/kpi {elapsed_ms:.0f}ms — 500ms 초과"


def test_checklist_3_round_report_under_10s(seeded_client):
    started = time.perf_counter()
    response = seeded_client.get(f"/api/v1/reports/rounds/{DEMO_ROUND}?refresh=true")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 10.0, f"/reports/rounds/{{id}} {elapsed:.2f}s — 10초 초과"


def test_checklist_4_before_after_reproduces_validated_figures(seeded_client):
    data = seeded_client.get(
        f"/api/v1/reports/before-after?rounds={BASELINE_ROUND},{DEMO_ROUND}"
    ).json()["data"]

    # 회신 소요시간 -60%
    assert data[Metric.RESPONSE_LEADTIME_H]["delta_pct"] == pytest.approx(-60.0, abs=1.0)
    # 규칙 준수율 +25pp
    assert data[Metric.RULE_COMPLIANCE]["delta_pp"] == pytest.approx(25.5, abs=0.6)


def test_checklist_5_four_risk_rules_detected(seeded):
    assert len(RISK_RULES) == 4
    assert {name for name, _ in RISK_RULES} == {
        "담당자_변경",
        "노쇼_예측",
        "면접관_피로도",
        "조직_회신_지연",
    }

    detected = {signal.type for signal in detect_risks(seeded, DEMO_ROUND)}
    assert detected == {name for name, _ in RISK_RULES}


# --- 서비스 기동/운영 엔드포인트 ---------------------------------------------


def test_healthz(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"]


def test_root_lists_service_identity(client):
    data = client.get("/").json()["data"]

    assert data["service"] == "07-audit-analytics"
    assert data["endpoints"]


def test_metrics_exposes_prometheus_text(seeded_client):
    response = seeded_client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "audit_events_received_total" in body
    assert "audit_events_by_type_total" in body
    assert "# TYPE" in body


def test_openapi_documents_all_routes(client):
    paths = client.get("/openapi.json").json()["paths"]

    for route in (
        "/api/v1/dashboard/kpi",
        "/api/v1/dashboard/organizations",
        "/api/v1/dashboard/risks",
        "/api/v1/audit/timeline",
        "/api/v1/audit/query",
        "/api/v1/audit/events",
        "/api/v1/reports/rounds/{round_id}",
        "/api/v1/reports/before-after",
    ):
        assert route in paths, f"{route} 가 OpenAPI에 없다"


def test_error_envelope_is_consistent(client):
    body = client.get("/api/v1/reports/rounds/R-NONE").json()

    assert body["data"] is None
    assert set(body["error"]) == {"code", "message"}


def test_success_envelope_is_consistent(client):
    body = client.get("/api/v1/dashboard/kpi?round_id=R-NONE").json()

    assert set(body) == {"data", "error"}
    assert body["error"] is None
