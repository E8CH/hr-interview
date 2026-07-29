"""리포트 — 회차 종합 · 단계별 소요 · 캐시 무효화 · Before/After"""
from __future__ import annotations

import time

import pytest

from app.domain.kpi import Metric
from app.events import EventType
from app.services.demo_data import BASELINE_ROUND, DEMO_ROUND
from app.services.event_collector import ingest_event
from app.services.report_generator import ReportGenerator, round_metric
from tests.conftest import make_event


# --- 회차 종합 리포트 ---------------------------------------------------------


def test_report_generation_within_10s(seeded):
    """회차 리포트 10초 이내 생성 (명세 test_report_generation)"""
    started = time.perf_counter()
    report, cache_hit = ReportGenerator(seeded).round_summary(DEMO_ROUND)
    elapsed = time.perf_counter() - started

    assert elapsed < 10.0, f"리포트 생성 {elapsed:.2f}s — 10초 초과"
    assert cache_hit is False
    assert report["round_id"] == DEMO_ROUND
    assert set(report) >= {
        "round_id",
        "duration_hours",
        "phases",
        "rule_compliance",
        "noshow_count",
        "repair_events",
        "kpi",
        "organizations",
        "risks",
        "generated_at",
    }


def test_report_phase_durations(seeded):
    report, _ = ReportGenerator(seeded).round_summary(DEMO_ROUND)
    phases = {p["phase"]: p["duration_h"] for p in report["phases"]}

    assert list(phases) == ["자료취합", "배포", "회신수집", "배치", "안내"]
    assert phases["자료취합"] == pytest.approx(0.1, abs=0.01)
    assert phases["배포"] == pytest.approx(0.3, abs=0.01)
    assert phases["회신수집"] == pytest.approx(1.8, abs=0.01)
    assert phases["배치"] == pytest.approx(0.05, abs=0.01)
    assert phases["안내"] == pytest.approx(0.1, abs=0.01)
    assert report["duration_hours"] == pytest.approx(3.2, abs=0.05)


def test_report_counts_noshow_and_repairs(seeded):
    report, _ = ReportGenerator(seeded).round_summary(DEMO_ROUND)

    assert report["noshow_count"] == 3
    assert report["repair_events"] == 1
    assert report["total_events"] == sum(report["event_counts"].values())


def test_report_includes_rule_compliance_from_scheduler_mock(seeded):
    report, _ = ReportGenerator(seeded).round_summary(DEMO_ROUND)
    compliance = report["rule_compliance"]

    assert compliance["overall"] == pytest.approx(90.5)
    assert compliance["rules"], "Service 04 규칙별 준수율(mock)이 포함되어야 한다"
    assert compliance["violations"] == 1


def test_report_endpoint_404_for_unknown_round(client):
    response = client.get("/api/v1/reports/rounds/R-NONE")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_report_endpoint_within_10s(seeded_client):
    """`/reports/rounds/{id}` 10초 이내 (완료 판정 체크리스트 3)"""
    started = time.perf_counter()
    response = seeded_client.get(f"/api/v1/reports/rounds/{DEMO_ROUND}")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 10.0
    assert response.headers["X-Cache"] == "MISS"


# --- 캐시 -------------------------------------------------------------------


def test_second_request_hits_cache(seeded_client):
    first = seeded_client.get(f"/api/v1/reports/rounds/{DEMO_ROUND}")
    second = seeded_client.get(f"/api/v1/reports/rounds/{DEMO_ROUND}")

    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert first.json()["data"]["generated_at"] == second.json()["data"]["generated_at"]


def test_cache_invalidation_on_new_event(seeded_client):
    """동일 회차에 새 이벤트가 도착하면 캐시가 무효화된다 (명세 test_cache_invalidation)"""
    seeded_client.get(f"/api/v1/reports/rounds/{DEMO_ROUND}")
    assert (
        seeded_client.get(f"/api/v1/reports/rounds/{DEMO_ROUND}").headers["X-Cache"]
        == "HIT"
    )

    seeded_client.post(
        "/api/v1/audit/events",
        json=make_event(
            EventType.NOSHOW_REPORTED,
            round_id=DEMO_ROUND,
            payload={"noshow_applicant_ids": ["A-9999"]},
            minutes=200,
        ),
    )

    refreshed = seeded_client.get(f"/api/v1/reports/rounds/{DEMO_ROUND}")
    assert refreshed.headers["X-Cache"] == "MISS"
    assert refreshed.json()["data"]["noshow_count"] == 4


def test_refresh_query_param_bypasses_cache(seeded_client):
    seeded_client.get(f"/api/v1/reports/rounds/{DEMO_ROUND}")
    forced = seeded_client.get(f"/api/v1/reports/rounds/{DEMO_ROUND}?refresh=true")

    assert forced.headers["X-Cache"] == "MISS"


def test_cached_round_summary_returns_none_without_cache(session):
    assert ReportGenerator(session).cached_round_summary("R-NONE") is None


# --- Before/After ------------------------------------------------------------


def test_before_after_reproduces_validated_figures(seeded):
    """Before/After 검증 수치 재현 — 회신 -60%, 규칙 준수 +25pp
    (완료 판정 체크리스트 4)
    """
    result = ReportGenerator(seeded).before_after(BASELINE_ROUND, DEMO_ROUND)

    leadtime = result[Metric.RESPONSE_LEADTIME_H]
    assert leadtime["before"] == pytest.approx(29.9)
    assert leadtime["after"] == pytest.approx(11.8, abs=0.05)
    assert leadtime["delta_pct"] == pytest.approx(-60.0, abs=1.0)

    compliance = result[Metric.RULE_COMPLIANCE]
    assert compliance["before"] == pytest.approx(65.0)
    assert compliance["after"] == pytest.approx(90.5)
    assert compliance["delta_pp"] == pytest.approx(25.5, abs=0.6)

    completion = result[Metric.RESPONSE_COMPLETION]
    assert completion["before"] == pytest.approx(86.0)
    assert completion["after"] == pytest.approx(92.0)
    assert completion["delta_pp"] == pytest.approx(6.0)

    assert result[Metric.ASSIGN_DURATION_H]["delta_pct"] < -90
    assert result[Metric.NOSHOW_RESPONSE_H]["delta_pct"] < -90


def test_before_after_endpoint(seeded_client):
    response = seeded_client.get(
        f"/api/v1/reports/before-after?rounds={BASELINE_ROUND},{DEMO_ROUND}"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {
        Metric.RESPONSE_LEADTIME_H,
        Metric.RESPONSE_COMPLETION,
        Metric.ASSIGN_DURATION_H,
        Metric.RULE_COMPLIANCE,
        Metric.NOSHOW_RESPONSE_H,
    }


def test_before_after_requires_exactly_two_rounds(seeded_client):
    response = seeded_client.get(f"/api/v1/reports/before-after?rounds={DEMO_ROUND}")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_before_after_404_for_unknown_round(seeded_client):
    response = seeded_client.get(
        f"/api/v1/reports/before-after?rounds=R-NOPE,{DEMO_ROUND}"
    )

    assert response.status_code == 404
    assert "R-NOPE" in response.json()["error"]["message"]


def test_before_after_omits_delta_when_a_side_is_missing(session):
    """한쪽 데이터가 없으면 델타를 지어내지 않는다"""
    ingest_event(
        session,
        make_event(
            EventType.SCHEDULE_GENERATED,
            round_id="R-AFTER",
            payload={"rule_compliance_overall": 0.9},
        ),
    )

    result = ReportGenerator(session).before_after("R-BEFORE", "R-AFTER")
    entry = result[Metric.RULE_COMPLIANCE]

    assert entry["after"] == pytest.approx(90.0)
    assert "before" not in entry
    assert "delta_pp" not in entry


@pytest.mark.parametrize(
    "raw, expected",
    [(None, None), (0.05, 0.05), (0.0499, 0.05), (11.8333, 11.8), (3.2, 3.2)],
)
def test_round_metric_keeps_sub_unit_precision(raw, expected):
    """0.05h(3분)가 소수 1자리 반올림에서 0.1로 뭉개지지 않아야 한다"""
    assert round_metric(raw) == expected
