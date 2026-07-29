"""프로젝션 — event_log → kpi_snapshots / org_response_stats"""
from __future__ import annotations

import pytest

from app.domain.kpi import Metric
from app.events import EventType
from app.infrastructure.repository import KpiRepository, OrgStatRepository
from app.services.event_collector import ingest_event, ingest_many
from app.services.projector import normalize_pct
from tests.conftest import make_event

ROUND = "R2026-Q3-01"


def latest(session, metric: str, default=None):
    return KpiRepository(session).latest_value(ROUND, metric, default)


def test_response_received_increments_completion_counter(session):
    """RESPONSE_RECEIVED → 회신완료 카운터 증가 (명세 test_projection)"""
    ingest_event(
        session,
        make_event(
            EventType.REQUEST_SENT,
            payload={"org": "제1기술원", "invitee_count": 3, "invitee_ids": ["A", "B", "C"]},
        ),
    )
    assert latest(session, Metric.RESPONSE_DONE, 0) == 0
    assert latest(session, Metric.RESPONSE_PENDING, 0) == 3

    for index, invitee in enumerate(["A", "B"], start=1):
        ingest_event(
            session,
            make_event(
                EventType.RESPONSE_RECEIVED,
                payload={"org": "제1기술원", "invitee_id": invitee, "response_hours": 6.0},
                minutes=index,
            ),
        )
        assert latest(session, Metric.RESPONSE_DONE, 0) == index

    assert latest(session, Metric.RESPONSE_PENDING, 0) == 1
    assert latest(session, Metric.RESPONSE_COMPLETION) == pytest.approx(200 / 3, abs=0.1)


def test_master_registered_sets_total_targets(session):
    ingest_event(
        session,
        make_event(EventType.MASTER_REGISTERED, payload={"applicant_count": 88}),
    )
    assert latest(session, Metric.TOTAL_TARGETS) == 88


def test_org_response_stats_track_mean_and_completion(session):
    """조직별 협업 온도계 원천 데이터 — 평균 회신 시간 · 완료율 · 지연 예측"""
    ingest_many(
        session,
        [
            make_event(
                EventType.REQUEST_SENT,
                payload={"org": "제3기술원", "invitee_count": 8,
                         "invitee_ids": [f"IV{i}" for i in range(8)]},
            ),
            *[
                make_event(
                    EventType.RESPONSE_RECEIVED,
                    payload={"org": "제3기술원", "invitee_id": f"IV{i}",
                             "response_hours": 52.0},
                    minutes=i + 1,
                )
                for i in range(5)
            ],
        ],
    )

    stat = OrgStatRepository(session).get(ROUND, "제3기술원")
    assert stat is not None
    assert stat.invited_count == 8
    assert stat.responded_count == 5
    assert stat.mean_hours == pytest.approx(52.0)
    assert stat.completion_rate == pytest.approx(62.5)
    assert stat.predicted_slow is True


def test_org_with_fast_response_is_not_predicted_slow(session):
    ingest_many(
        session,
        [
            make_event(
                EventType.REQUEST_SENT,
                payload={"org": "제1기술원", "invitee_count": 2, "invitee_ids": ["a", "b"]},
            ),
            make_event(
                EventType.RESPONSE_RECEIVED,
                payload={"org": "제1기술원", "invitee_id": "a", "response_hours": 6.0},
                minutes=1,
            ),
            make_event(
                EventType.RESPONSE_RECEIVED,
                payload={"org": "제1기술원", "invitee_id": "b", "response_hours": 6.0},
                minutes=2,
            ),
        ],
    )
    stat = OrgStatRepository(session).get(ROUND, "제1기술원")
    assert stat.completion_rate == pytest.approx(100.0)
    assert stat.predicted_slow is False


def test_response_hours_falls_back_to_request_timestamp(session):
    """payload에 response_hours가 없으면 REQUEST_SENT → 회신 경과로 계산한다"""
    ingest_many(
        session,
        [
            make_event(
                EventType.REQUEST_SENT,
                payload={"org": "제2사업부", "invitee_count": 1, "invitee_ids": ["x"]},
                minutes=0,
            ),
            make_event(
                EventType.RESPONSE_RECEIVED,
                payload={"org": "제2사업부", "invitee_id": "x"},
                minutes=120,
            ),
        ],
    )
    stat = OrgStatRepository(session).get(ROUND, "제2사업부")
    assert stat.mean_hours == pytest.approx(2.0, abs=0.01)


def test_schedule_lock_projects_assign_rate_and_exec_seconds(session):
    ingest_many(
        session,
        [
            make_event(EventType.MASTER_REGISTERED, payload={"applicant_count": 88}),
            make_event(
                EventType.SCHEDULE_GENERATED,
                payload={"total_assigned": 78, "rule_compliance_overall": 0.905},
                minutes=140,
            ),
            make_event(
                EventType.SCHEDULE_LOCKED,
                payload={"assignments_count": 78, "execution_seconds": 3.2},
                minutes=143,
            ),
        ],
    )
    assert latest(session, Metric.ASSIGN_RATE) == pytest.approx(78 / 88 * 100, abs=0.1)
    assert latest(session, Metric.RULE_COMPLIANCE) == pytest.approx(90.5)
    assert latest(session, Metric.EXEC_SECONDS) == pytest.approx(3.2)
    # 배치 소요 = SCHEDULE_GENERATED → LOCKED (3분 = 0.05h)
    assert latest(session, Metric.ASSIGN_DURATION_H) == pytest.approx(0.05, abs=0.001)


def test_noshow_and_repair_project_response_time(session):
    ingest_many(
        session,
        [
            make_event(
                EventType.NOSHOW_REPORTED,
                payload={"noshow_applicant_ids": ["A-1", "A-2", "A-3"]},
                minutes=180,
            ),
            make_event(
                EventType.REPAIR_EXECUTED,
                payload={"plan_type": "A", "rebooked": 2},
                minutes=186,
            ),
        ],
    )
    assert latest(session, Metric.NOSHOW_COUNT) == 3
    assert latest(session, Metric.REPAIR_COUNT) == 1
    assert latest(session, Metric.NOSHOW_RESPONSE_H) == pytest.approx(0.1, abs=0.001)


@pytest.mark.parametrize(
    "raw, expected",
    [(0.905, 90.5), (90.5, 90.5), (1.0, 100.0), (0.0, 0.0), (100, 100.0)],
)
def test_normalize_pct_handles_ratio_and_percent(raw, expected):
    """0~1 비율과 0~100 백분율이 섞여 들어와도 동일하게 정규화"""
    assert normalize_pct(raw) == pytest.approx(expected)


def test_unknown_event_type_is_stored_without_projection(session):
    """카탈로그에 없는 이벤트도 감사 로그는 남긴다 (프로젝션만 생략)"""
    from app.infrastructure.repository import EventRepository

    result = ingest_event(session, make_event("hr.some.future.event", payload={"a": 1}))
    assert result.status == "stored"
    assert EventRepository(session).total_count() == 1


def test_kpi_snapshots_are_strictly_monotonic(session):
    """동일 타임스탬프 연속 기록에도 '최신' 순서가 모호해지지 않는다"""
    kpi = KpiRepository(session)
    for value in (1.0, 2.0, 3.0):
        kpi.record(ROUND, Metric.RESPONSE_DONE, value)
    session.commit()
    assert kpi.latest_value(ROUND, Metric.RESPONSE_DONE) == 3.0


def test_org_without_name_falls_back_to_placeholder(session):
    ingest_event(
        session,
        make_event(EventType.RESPONSE_RECEIVED, payload={"invitee_id": "z"}),
    )
    orgs = {s.org for s in OrgStatRepository(session).by_round(ROUND)}
    assert "미지정" in orgs
