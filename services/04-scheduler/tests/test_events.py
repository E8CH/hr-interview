"""이벤트 발행/구독 테스트 — 공통 계약 봉투 준수 확인"""
from __future__ import annotations

import json

from app import events
from app.infrastructure.contracts import EventType
from app.infrastructure.event_bus import EventBus, get_event_bus


def test_schedule_generated_event_follows_envelope(client, sample_round_id):
    bus = get_event_bus()
    client.post(
        "/api/v1/schedules/generate",
        json={"round_id": sample_round_id, "plan_id": "evt-1", "algorithm": "v5"},
    )
    published = bus.published_of(EventType.SCHEDULE_GENERATED)

    assert len(published) == 1
    env = published[0]
    assert env.producer == "scheduler"
    assert env.round_id == sample_round_id
    assert env.correlation_id
    assert env.event_id
    assert set(env.payload) == {
        "schedule_id",
        "total_assigned",
        "coverage_pct",
        "rule_compliance_overall",
    }


def test_schedule_locked_event_published(client, generated):
    bus = get_event_bus()
    resp = client.post(
        f"/api/v1/schedules/{generated['schedule_id']}/lock", json={"lock_level": "CONFIRMED"}
    )
    assert resp.status_code == 200

    published = bus.published_of(EventType.SCHEDULE_LOCKED)
    assert len(published) == 1
    payload = published[0].payload
    assert payload["lock_level"] == "CONFIRMED"
    assert payload["assignments_count"] == generated["total_assigned"]
    assert payload["schedule_id"] == generated["schedule_id"]


def test_schedule_locked_reaches_downstream_subscribers(client, sample_round_id):
    """SCHEDULE_LOCKED가 공용 채널로 실제 전달된다 (Service 05·06 수신 경로)

    이 서비스가 보장할 수 있는 범위 = 공통 계약 봉투로 공용 채널에 실제로 실린다는 것.
    05·06 대신 같은 채널을 구독하는 소비자를 붙여 수신을 확인한다.
    """
    from app.config import settings

    bus = get_event_bus()
    downstream = bus._client.pubsub(ignore_subscribe_messages=True)
    downstream.subscribe(settings.event_channel)

    created = client.post(
        "/api/v1/schedules/generate",
        json={"round_id": sample_round_id, "plan_id": "downstream", "algorithm": "v5"},
    ).json()["data"]
    client.post(
        f"/api/v1/schedules/{created['schedule_id']}/lock", json={"lock_level": "CONFIRMED"}
    )

    # fakeredis의 get_message는 큐를 한 박자 늦게 비우므로 고정 횟수만큼 폴링한다
    received = []
    for _ in range(20):
        msg = downstream.get_message(timeout=0.05)
        if msg and msg.get("type") == "message":
            received.append(json.loads(msg["data"]))
    downstream.close()

    locked = [e for e in received if e["event_type"] == EventType.SCHEDULE_LOCKED]
    assert len(locked) == 1
    envelope = locked[0]
    # 공통 계약 봉투 필드가 모두 실려 있어야 05·06이 파싱할 수 있다
    assert set(envelope) == {
        "event_id",
        "event_type",
        "timestamp",
        "round_id",
        "producer",
        "correlation_id",
        "payload",
    }
    assert envelope["producer"] == "scheduler"
    assert envelope["round_id"] == sample_round_id
    assert envelope["payload"]["lock_level"] == "CONFIRMED"
    assert envelope["payload"]["schedule_id"] == created["schedule_id"]


def test_rule_violated_event_on_hard_violation(sample_round_id):
    """하드 위반이 있으면 RULE_VIOLATED를 발행한다"""
    bus = get_event_bus()
    bus.reset()
    events.publish_rule_violated(
        round_id=sample_round_id,
        schedule_id="s-1",
        violations=[{"code": "TEAM_CONFLICT", "team": "AI솔루션팀"}],
    )
    published = bus.published_of(EventType.RULE_VIOLATED)
    assert published[0].payload["violation_count"] == 1


def test_subscribes_to_response_received_and_distribution_approved():
    bus = EventBus(url="fakeredis://", channel="test.events")
    seen = []
    bus.subscribe(EventType.RESPONSE_RECEIVED, lambda e: seen.append(e.event_type))
    bus.subscribe(EventType.DISTRIBUTION_APPROVED, lambda e: seen.append(e.event_type))

    bus.publish(
        EventType.RESPONSE_RECEIVED,
        round_id="R1",
        payload={"response_id": "r1", "invitee_id": "IV101"},
        correlation_id="c1",
        producer="response-collector",
    )
    bus.publish(
        EventType.DISTRIBUTION_APPROVED,
        round_id="R1",
        payload={"plan_id": "p1", "approver": "hr"},
        correlation_id="c1",
        producer="distributor",
    )
    assert seen == [EventType.RESPONSE_RECEIVED, EventType.DISTRIBUTION_APPROVED]


def test_readiness_tracks_subscribed_events(client, sample_round_id):
    events.reset_readiness()
    events.register_subscriptions()
    bus = get_event_bus()

    bus.publish(
        EventType.DISTRIBUTION_APPROVED,
        round_id=sample_round_id,
        payload={"plan_id": "plan-42", "approver": "hr"},
        correlation_id="c1",
        producer="distributor",
    )
    for i in range(3):
        bus.publish(
            EventType.RESPONSE_RECEIVED,
            round_id=sample_round_id,
            payload={"response_id": f"r{i}", "invitee_id": f"IV10{i}"},
            correlation_id="c1",
            producer="response-collector",
        )

    resp = client.get(f"/api/v1/rounds/{sample_round_id}/readiness")
    data = resp.json()["data"]
    assert data["distribution_approved"] is True
    assert data["plan_id"] == "plan-42"
    assert data["responses"] == 3


def test_handle_raw_ignores_own_events():
    bus = EventBus(url="fakeredis://", channel="test.raw")
    seen = []
    bus.subscribe(EventType.SCHEDULE_GENERATED, lambda e: seen.append(e))

    own = {
        "event_id": "e1",
        "event_type": EventType.SCHEDULE_GENERATED,
        "timestamp": "2026-07-29T10:00:00Z",
        "round_id": "R1",
        "producer": "scheduler",
        "correlation_id": "c1",
        "payload": {},
    }
    assert bus.handle_raw(json.dumps(own)) is None
    assert seen == []

    foreign = dict(own, producer="repair-engine")
    assert bus.handle_raw(json.dumps(foreign)) is not None
    assert len(seen) == 1


def test_handle_raw_drops_malformed():
    bus = EventBus(url="fakeredis://", channel="test.bad")
    assert bus.handle_raw("not-json") is None


def test_listener_thread_starts_and_stops():
    bus = EventBus(url="fakeredis://", channel="test.listener")
    bus.start_listener()
    bus.start_listener()  # 중복 호출 무해
    bus.stop_listener()


def test_handler_exception_is_isolated():
    bus = EventBus(url="fakeredis://", channel="test.err")
    ok_calls = []

    def boom(_):
        raise RuntimeError("handler failure")

    bus.subscribe(EventType.SCHEDULE_GENERATED, boom)
    bus.subscribe(EventType.SCHEDULE_GENERATED, lambda e: ok_calls.append(e))
    bus.publish(EventType.SCHEDULE_GENERATED, "R1", {}, "c1")

    assert len(ok_calls) == 1
