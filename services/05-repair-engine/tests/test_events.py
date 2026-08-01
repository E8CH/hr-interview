"""이벤트 발행 · 구독 테스트 (봉투 규격 포함)"""
from shared.contracts.events import EventEnvelope

from app import events
from app.infrastructure.db import ScheduleSnapshotRow
from app.infrastructure.event_bus import event_bus

from tests.conftest import ROUND_ID, SCHEDULE_ID


def test_envelope_conforms_to_shared_contract():
    body = events.publish_repair_executed(
        ROUND_ID, "corr-1", "ev-1", "A_safe", 10, 3, SCHEDULE_ID, "HR")
    envelope = EventEnvelope.model_validate(body)
    assert envelope.event_type == "REPAIR_EXECUTED"
    assert envelope.producer == "repair-engine"
    assert envelope.round_id == ROUND_ID
    assert envelope.correlation_id == "corr-1"
    assert envelope.payload["rebooked"] == 10


def test_all_published_event_types():
    events.publish_noshow_reported(ROUND_ID, "c", ["1"], "HR")
    events.publish_repair_executed(ROUND_ID, "c", "ev", "A_safe", 1, 0, SCHEDULE_ID, "HR")
    events.publish_participant_deferred(ROUND_ID, "c", "ev", ["1"], "이월")
    events.publish_slot_reopened(ROUND_ID, "c", "ev", [{"day": "1일차", "hour": "09시",
                                                        "interviewer_id": "IV001",
                                                        "team": "AI솔루션팀"}])
    types = [e["event_type"] for e in event_bus.published()]
    assert types == ["NOSHOW_REPORTED", "REPAIR_EXECUTED",
                     "PARTICIPANT_DEFERRED", "SLOT_REOPENED"]
    for body in event_bus.published():
        EventEnvelope.model_validate(body)


def test_correlation_id_shared_across_chain(client, noshow_13):
    r = client.post("/api/v1/repair/noshow", json={
        "round_id": ROUND_ID, "schedule_id": SCHEDULE_ID,
        "noshow_applicant_ids": noshow_13, "reported_by": "HR"})
    event_id = r.json()["data"]["event_id"]
    plans = client.get(f"/api/v1/repair/plans/{event_id}").json()["data"]["plans"]
    plan_b = next(p for p in plans if p["type"] == "B_defer")
    client.post(f"/api/v1/repair/plans/{event_id}/select",
                json={"plan_id": plan_b["plan_id"], "selected_by": "HR"})

    chain = [e for e in event_bus.published()
             if e["event_type"] in (events.REPAIR_EXECUTED, events.PARTICIPANT_DEFERRED,
                                    events.SLOT_REOPENED)]
    assert len({e["correlation_id"] for e in chain}) == 1
    assert all(e["round_id"] == ROUND_ID for e in chain)


def test_schedule_locked_subscription_prefetches_snapshot(client, session):
    """SCHEDULE_LOCKED 수신 → 재편성 대상 시간표를 미리 적재한다"""
    assert session.get(ScheduleSnapshotRow, "SCH-FROM-EVENT") is None

    event_bus.publish({
        "event_id": "e1", "event_type": events.SCHEDULE_LOCKED,
        "timestamp": "2026-07-29T10:00:00Z", "round_id": ROUND_ID,
        "producer": "scheduler", "correlation_id": "corr-x",
        "payload": {"schedule_id": "SCH-FROM-EVENT", "lock_level": "LOCKED",
                    "assignments_count": 65},
    })

    row = session.get(ScheduleSnapshotRow, "SCH-FROM-EVENT")
    assert row is not None
    assert row.payload["schedule_id"] == "SCH-FROM-EVENT"
    assert len(row.payload["assignments"]) == 65


def test_schedule_locked_ignores_malformed_payload(client, session):
    events.handle_schedule_locked({"round_id": ROUND_ID, "payload": {}})
    events.handle_schedule_locked({"payload": {"schedule_id": "X"}})
    # 예외 없이 무시되고, 스냅샷도 만들어지지 않는다
    assert session.get(ScheduleSnapshotRow, "X") is None


def test_handler_error_does_not_break_publish():
    def boom(_envelope):
        raise RuntimeError("subscriber down")

    event_bus.subscribe("TEST_EVENT", boom)
    event_bus.publish({"event_type": "TEST_EVENT", "round_id": ROUND_ID,
                       "producer": "t", "correlation_id": "c", "payload": {}})
    assert event_bus.published("TEST_EVENT")


def test_published_filter_and_reset():
    events.publish_noshow_reported(ROUND_ID, "c", ["1"], "HR")
    assert len(event_bus.published(events.NOSHOW_REPORTED)) == 1
    assert len(event_bus.published("NOPE")) == 0
    event_bus.reset()
    assert event_bus.published() == []
