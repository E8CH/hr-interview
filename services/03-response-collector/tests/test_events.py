"""이벤트 발행/구독 — 공통 계약(00_SHARED_CONTRACT §4) 준수 검증"""
import json

import pytest

from app.events import (
    PUBLISHED_EVENTS,
    SUBSCRIBED_EVENTS,
    EventEnvelope,
    EventType,
    make_envelope,
)
from app.infrastructure.event_bus import CHANNEL, EventBus
from app.services import reminder_service, response_service
from app.subscribers import on_distribution_approved
from tests.conftest import shift_sent_at


def test_published_events_are_in_shared_catalog():
    assert set(PUBLISHED_EVENTS) == {
        EventType.REQUEST_SENT,
        EventType.RESPONSE_RECEIVED,
        EventType.REMINDER_SENT,
        EventType.NON_RESPONDER_ESCALATED,
    }
    assert set(SUBSCRIBED_EVENTS) == {EventType.DISTRIBUTION_APPROVED}


def test_envelope_shape():
    envelope = make_envelope(
        EventType.REQUEST_SENT, round_id="R2026-Q3-01", correlation_id="corr-1", payload={"a": 1}
    )
    dumped = json.loads(envelope.model_dump_json())
    assert set(dumped) == {
        "event_id",
        "event_type",
        "timestamp",
        "round_id",
        "producer",
        "correlation_id",
        "payload",
    }
    assert dumped["producer"] == "response-collector"


def test_full_lifecycle_event_order(db, created_request, valid_payload, bus):
    """3️⃣ 단계 라이프사이클: REQUEST_SENT → RESPONSE_RECEIVED → REMINDER_SENT → ESCALATED"""
    response_service.submit_response(db, created_request.invitees[0], valid_payload)
    shift_sent_at(db, created_request, 69)
    reminder_service.run_reminder_cycle(db)

    types = [e.event_type for e in bus.history()]
    assert types[0] == EventType.REQUEST_SENT
    assert types[1] == EventType.RESPONSE_RECEIVED
    assert EventType.REMINDER_SENT in types
    assert EventType.NON_RESPONDER_ESCALATED in types
    assert types.index(EventType.REMINDER_SENT) < len(types)

    # 회신자 1명은 리마인더 대상에서 제외 → 2건씩
    assert types.count(EventType.REMINDER_SENT) == 2
    assert types.count(EventType.NON_RESPONDER_ESCALATED) == 2


def test_publish_goes_through_redis_channel():
    """fakeredis 채널로 실제 발행되는지 확인."""
    bus = EventBus("fakeredis://")
    pubsub = bus.client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(CHANNEL)

    envelope = make_envelope(EventType.REQUEST_SENT, "R2026-Q3-01", "corr", {"x": 1})
    bus.publish(envelope)

    # 첫 폴링은 구독 확인 메시지를 소비할 수 있으므로 몇 차례 시도한다.
    message = None
    for _ in range(10):
        message = pubsub.get_message(timeout=0.5)
        if message is not None:
            break

    assert message is not None, "채널로 발행된 메시지를 받지 못했다"
    assert json.loads(message["data"])["event_id"] == envelope.event_id
    pubsub.close()


def test_subscriber_receives_in_process():
    bus = EventBus("fakeredis://")
    received = []
    bus.subscribe(EventType.DISTRIBUTION_APPROVED, received.append)

    bus.publish(make_envelope(EventType.DISTRIBUTION_APPROVED, "R1", "c1", {"plan_id": "p"}))
    bus.publish(make_envelope(EventType.REQUEST_SENT, "R1", "c1", {}))

    assert len(received) == 1
    assert received[0].payload["plan_id"] == "p"


def test_handler_exception_does_not_break_publish():
    bus = EventBus("fakeredis://")

    def boom(_):
        raise RuntimeError("의도적 실패")

    bus.subscribe(EventType.REQUEST_SENT, boom)
    bus.publish(make_envelope(EventType.REQUEST_SENT, "R1", "c1", {}))
    assert len(bus.history(EventType.REQUEST_SENT)) == 1


def test_counts_tracked():
    bus = EventBus("fakeredis://")
    for _ in range(3):
        bus.publish(make_envelope(EventType.REMINDER_SENT, "R1", "c1", {}))
    assert bus.counts()[EventType.REMINDER_SENT] == 3

    bus.reset()
    assert bus.counts() == {}


# --- DISTRIBUTION_APPROVED 구독 → 자동 발송 ---
def test_distribution_approved_triggers_auto_send(db, bus):
    envelope = make_envelope(
        EventType.DISTRIBUTION_APPROVED,
        round_id="R2026-Q3-02",
        correlation_id="corr-dist",
        payload={"plan_id": "plan-auto-1", "approver": "hr@lge.com", "total_applicants": 120},
    )
    on_distribution_approved(envelope)

    from app.domain.request import Request

    request = db.query(Request).filter(Request.plan_id == "plan-auto-1").one()
    assert request.round_id == "R2026-Q3-02"
    assert request.correlation_id == "corr-dist"  # 이벤트 체인 추적
    assert len(request.invitees) == 15

    sent = bus.history(EventType.REQUEST_SENT)
    assert len(sent) == 1
    assert sent[0].correlation_id == "corr-dist"


def test_distribution_approved_is_idempotent(db, bus):
    envelope = make_envelope(
        EventType.DISTRIBUTION_APPROVED,
        "R2026-Q3-02",
        "corr-dist",
        {"plan_id": "plan-auto-2", "approver": "hr@lge.com"},
    )
    on_distribution_approved(envelope)
    on_distribution_approved(envelope)

    from app.domain.request import Request

    assert db.query(Request).filter(Request.plan_id == "plan-auto-2").count() == 1
    assert len(bus.history(EventType.REQUEST_SENT)) == 1


def test_distribution_approved_without_plan_id_is_ignored(db, bus):
    on_distribution_approved(
        make_envelope(EventType.DISTRIBUTION_APPROVED, "R1", "c1", {"approver": "hr@lge.com"})
    )
    assert bus.history(EventType.REQUEST_SENT) == []


def test_subscription_wired_through_bus(db, bus):
    """앱이 등록한 구독을 통해 실제로 자동 발송이 일어나는지."""
    from app.subscribers import register_subscribers

    register_subscribers()
    bus.publish(
        make_envelope(
            EventType.DISTRIBUTION_APPROVED,
            "R2026-Q3-03",
            "corr-wired",
            {"plan_id": "plan-wired", "approver": "hr@lge.com"},
        )
    )

    from app.domain.request import Request

    assert db.query(Request).filter(Request.plan_id == "plan-wired").count() == 1


@pytest.mark.parametrize(
    "event_type",
    [EventType.REQUEST_SENT, EventType.RESPONSE_RECEIVED, EventType.REMINDER_SENT],
)
def test_envelope_roundtrip(event_type):
    envelope = make_envelope(event_type, "R2026-Q3-01", "corr", {"k": "v"})
    restored = EventEnvelope(**json.loads(envelope.model_dump_json()))
    assert restored.event_type == event_type
    assert restored.payload == {"k": "v"}
