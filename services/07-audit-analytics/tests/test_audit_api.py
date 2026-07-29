"""감사 API — 타임라인 · 복합 질의 · mock 이벤트 주입 · 수집 현황"""
from __future__ import annotations

from app.events import ALL_EVENT_TYPES, EventType
from app.services.demo_data import DEMO_ROUND
from tests.conftest import make_event

ROUND = "R2026-Q3-01"


def test_inject_endpoint_accepts_single_event(client):
    """PoC — 다른 서비스 이벤트를 mock으로 주입할 수 있어야 한다 (규칙 6)"""
    envelope = make_event(
        EventType.MASTER_REGISTERED, payload={"applicant_count": 88}
    )

    response = client.post("/api/v1/audit/events", json=envelope)

    assert response.status_code == 202
    data = response.json()["data"]
    assert data == {
        "accepted": 1,
        "total": 1,
        "results": [
            {
                "event_id": envelope["event_id"],
                "event_type": EventType.MASTER_REGISTERED,
                "status": "stored",
                "reason": None,
            }
        ],
    }


def test_inject_endpoint_accepts_batch(client):
    batch = [
        make_event(event_type, minutes=index)
        for index, event_type in enumerate(ALL_EVENT_TYPES)
    ]

    response = client.post("/api/v1/audit/events", json=batch)

    assert response.status_code == 202
    assert response.json()["data"]["accepted"] == 18


def test_injected_events_flow_through_to_dashboard(client):
    """주입 → 수집 → 프로젝션 → 대시보드까지 한 경로로 이어진다"""
    client.post(
        "/api/v1/audit/events",
        json=[
            make_event(EventType.MASTER_REGISTERED, payload={"applicant_count": 40}),
            make_event(
                EventType.REQUEST_SENT,
                payload={"org": "제1기술원", "invitee_count": 4,
                         "invitee_ids": ["a", "b", "c", "d"]},
                minutes=1,
            ),
            make_event(
                EventType.RESPONSE_RECEIVED,
                payload={"org": "제1기술원", "invitee_id": "a", "response_hours": 6.0},
                minutes=2,
            ),
        ],
    )

    kpi = client.get(f"/api/v1/dashboard/kpi?round_id={ROUND}").json()["data"]
    assert kpi["총_대상자"] == 40
    assert kpi["회신_완료"] == 1
    assert kpi["회신_대기"] == 3


def test_inject_rejects_event_without_type(client):
    response = client.post("/api/v1/audit/events", json={"round_id": ROUND})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_duplicate_injection_is_reported_not_stored_twice(client):
    envelope = make_event(EventType.REMINDER_SENT, payload={"level": 1})

    client.post("/api/v1/audit/events", json=envelope)
    second = client.post("/api/v1/audit/events", json=envelope)

    assert second.json()["data"]["accepted"] == 0
    assert second.json()["data"]["results"][0]["status"] == "duplicate"


def test_timeline_returns_events_in_chronological_order(seeded_client):
    rows = seeded_client.get(
        f"/api/v1/audit/timeline?round_id={DEMO_ROUND}"
    ).json()["data"]

    assert rows
    stamps = [row["timestamp"] for row in rows]
    assert stamps == sorted(stamps)
    assert rows[0]["event_type"] == EventType.MASTER_REGISTERED
    assert set(rows[0]) >= {
        "event_id",
        "event_type",
        "timestamp",
        "round_id",
        "producer",
        "correlation_id",
        "payload",
    }


def test_timeline_filters_by_multiple_event_types(seeded_client):
    rows = seeded_client.get(
        f"/api/v1/audit/timeline?round_id={DEMO_ROUND}"
        f"&event_type={EventType.NOSHOW_REPORTED},{EventType.REPAIR_EXECUTED}"
    ).json()["data"]

    assert {row["event_type"] for row in rows} == {
        EventType.NOSHOW_REPORTED,
        EventType.REPAIR_EXECUTED,
    }


def test_timeline_paginates(seeded_client):
    page1 = seeded_client.get(
        f"/api/v1/audit/timeline?round_id={DEMO_ROUND}&limit=5"
    ).json()["data"]
    page2 = seeded_client.get(
        f"/api/v1/audit/timeline?round_id={DEMO_ROUND}&limit=5&offset=5"
    ).json()["data"]

    assert len(page1) == len(page2) == 5
    assert {r["event_id"] for r in page1}.isdisjoint({r["event_id"] for r in page2})


def test_query_filters_by_producer_and_type(seeded_client):
    body = seeded_client.post(
        "/api/v1/audit/query",
        json={
            "round_id": DEMO_ROUND,
            "producer": "scheduler",
            "event_types": [EventType.SCHEDULE_LOCKED],
        },
    ).json()["data"]

    assert body["count"] == 1
    assert body["events"][0]["producer"] == "scheduler"


def test_query_filters_by_actor_inside_payload(seeded_client):
    body = seeded_client.post(
        "/api/v1/audit/query", json={"round_id": DEMO_ROUND, "actor": "HR김민지"}
    ).json()["data"]

    assert body["count"] >= 1
    assert all(
        "HR김민지" in str(event["payload"]) for event in body["events"]
    )


def test_query_filters_by_time_window(seeded_client):
    body = seeded_client.post(
        "/api/v1/audit/query",
        json={
            "round_id": DEMO_ROUND,
            "from": "2026-07-29T11:00:00",
            "to": "2026-07-29T12:30:00",
        },
    ).json()["data"]

    assert body["count"] >= 1
    for event in body["events"]:
        assert "2026-07-29T11:00:00" <= event["timestamp"] <= "2026-07-29T12:30:00"


def test_query_rejects_inverted_time_window(client):
    response = client.post(
        "/api/v1/audit/query",
        json={"from": "2026-07-29T12:00:00", "to": "2026-07-29T09:00:00"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_query_by_correlation_id_traces_a_whole_round(seeded_client):
    first = seeded_client.get(
        f"/api/v1/audit/timeline?round_id={DEMO_ROUND}&limit=1"
    ).json()["data"][0]

    body = seeded_client.post(
        "/api/v1/audit/query", json={"correlation_id": first["correlation_id"]}
    ).json()["data"]

    assert body["count"] >= 18


def test_event_stats_report_full_catalog_coverage(seeded_client):
    stats = seeded_client.get("/api/v1/audit/events/stats").json()["data"]

    assert stats["catalog_size"] == 18
    assert stats["missing_types"] == []
    assert sorted(stats["covered_types"]) == sorted(ALL_EVENT_TYPES)
    assert stats["total_events"] > 0


def test_event_stats_on_empty_db_lists_everything_missing(client):
    stats = client.get("/api/v1/audit/events/stats").json()["data"]

    assert stats["total_events"] == 0
    assert sorted(stats["missing_types"]) == sorted(ALL_EVENT_TYPES)
