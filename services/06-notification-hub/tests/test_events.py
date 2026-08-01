"""이벤트 구독 — 6종 트리거가 알림을 큐에 적재하는지 검증"""
from __future__ import annotations

import json

from app.contracts.events import EventType
from app.domain.models import Notification
from app.events import (
    PUBLISHED_EVENTS,
    SUBSCRIBED_EVENTS,
    extract_recipients,
    on_distribution_approved,
    on_integrity_violated,
    on_non_responder_escalated,
    on_participant_deferred,
    on_repair_executed,
    on_reminder_sent,
    on_request_sent,
    on_schedule_locked,
    register_subscribers,
)
from app.infrastructure.event_bus import EVENT_CHANNEL, get_event_bus

ROUND = "R2026-Q3-01"


def _envelope(event_type: str, payload: dict) -> dict:
    return {
        "event_id": "evt-1",
        "event_type": event_type,
        "timestamp": "2026-07-29T10:00:00Z",
        "round_id": ROUND,
        "producer": "some-service",
        "correlation_id": "corr-1",
        "payload": payload,
    }


def _rows(session, template_id: str) -> list[Notification]:
    return (
        session.query(Notification)
        .filter(Notification.template_id == template_id)
        .all()
    )


def test_catalog_matches_contract():
    """명세 06 의 6종 + 00_SHARED_CONTRACT 카탈로그가 06 을 구독자로 지정한 2종."""
    assert set(SUBSCRIBED_EVENTS) == {
        EventType.DISTRIBUTION_APPROVED,
        EventType.REQUEST_SENT,
        EventType.REMINDER_SENT,
        EventType.NON_RESPONDER_ESCALATED,
        EventType.SCHEDULE_LOCKED,
        EventType.REPAIR_EXECUTED,
        EventType.PARTICIPANT_DEFERRED,
        EventType.INTEGRITY_VIOLATED,
    }
    assert PUBLISHED_EVENTS == (
        "NOTIFICATION_SENT",
        "NOTIFICATION_FAILED",
        "NOTIFICATION_OPENED",
    )


def test_register_subscribers_covers_all(db):
    bus = get_event_bus()
    register_subscribers(bus)
    assert set(bus.subscribed_types()) == set(SUBSCRIBED_EVENTS)


def test_distribution_approved_broadcasts_applicant_invite(session):
    ids = on_distribution_approved(
        _envelope(
            EventType.DISTRIBUTION_APPROVED,
            {
                "plan_id": "P1",
                "approver": "hr01",
                "total_applicants": 2,
                "recipients": [
                    {"email": "a1@x.com", "context": {"name": "새한별", "day": "1일차", "hour": "09시"}},
                    {"email": "a2@x.com", "context": {"name": "홍길동", "day": "2일차", "hour": "10시"}},
                ],
            },
        )
    )
    assert len(ids) == 2
    rows = _rows(session, "applicant_invite")
    assert {row.recipient for row in rows} == {"a1@x.com", "a2@x.com"}
    assert all(row.status == "queued" for row in rows)
    assert all(row.round_id == ROUND for row in rows)
    assert all(row.correlation_id == "corr-1" for row in rows)
    assert "새한별" in next(r for r in rows if r.recipient == "a1@x.com").body


def test_request_sent_triggers_invite(session):
    ids = on_request_sent(
        _envelope(
            EventType.REQUEST_SENT,
            {
                "request_id": "REQ1",
                "invitee_count": 1,
                "deadline": "2026-07-31T18:00:00",
                "form_link": "https://hr.lge.com/form/abc",
                "invitees": [{"email": "iv1@lge.com", "name": "이지훈"}],
            },
        )
    )
    assert len(ids) == 1
    row = _rows(session, "invite")[0]
    assert row.recipient == "iv1@lge.com"
    assert "이지훈" in row.body
    assert "https://hr.lge.com/form/abc" in row.body


def test_reminder_sent_selects_level_template(session):
    for level, template_id in [(1, "reminder_l1"), (2, "reminder_l2"), (3, "reminder_l3")]:
        on_reminder_sent(
            _envelope(
                EventType.REMINDER_SENT,
                {
                    "invitee_id": "IV001",
                    "level": level,
                    "channel": "email",
                    "email": f"iv{level}@lge.com",
                    "name": "이지훈",
                    "deadline": "2026-07-31 18:00",
                    "form_link": "https://hr.lge.com/form/abc",
                },
            )
        )
    for level, template_id in [(1, "reminder_l1"), (2, "reminder_l2"), (3, "reminder_l3")]:
        rows = _rows(session, template_id)
        assert len(rows) == 1
        assert rows[0].recipient == f"iv{level}@lge.com"
    assert "상급자" in _rows(session, "reminder_l3")[0].body


def test_reminder_level_is_clamped_and_channel_sanitised(session):
    on_reminder_sent(
        _envelope(
            EventType.REMINDER_SENT,
            {
                "invitee_id": "IV001",
                "level": 99,
                "channel": "carrier-pigeon",
                "recipient": "iv@lge.com",
                "name": "이지훈",
                "deadline": "d",
                "form_link": "f",
            },
        )
    )
    rows = _rows(session, "reminder_l3")
    assert len(rows) == 1
    assert rows[0].channel == "email"


def test_reminder_with_bad_level_defaults_to_l1(session):
    on_reminder_sent(
        _envelope(
            EventType.REMINDER_SENT,
            {"level": "abc", "recipient": "x@lge.com", "name": "이", "deadline": "d", "form_link": "f"},
        )
    )
    assert len(_rows(session, "reminder_l1")) == 1


def test_non_responder_escalated_sends_final_reminder(session):
    """00_SHARED_CONTRACT 카탈로그: NON_RESPONDER_ESCALATED (03 → 06)"""
    ids = on_non_responder_escalated(
        _envelope(
            EventType.NON_RESPONDER_ESCALATED,
            {
                "invitee_id": "IV001",
                "email": "iv1@lge.com",
                "name": "이지훈",
                "deadline": "2026-07-31 18:00",
                "form_link": "https://hr.lge.com/form/abc",
                "supervisor": "김팀장",
                "supervisor_email": "lead@lge.com",
            },
        )
    )
    assert len(ids) == 1
    row = _rows(session, "reminder_l3")[0]
    assert row.recipient == "iv1@lge.com"
    assert row.cc == ["lead@lge.com"]  # 상급자 CC
    assert "김팀장" in row.body
    assert "이지훈" in row.body


def test_non_responder_escalated_with_recipient_list(session):
    ids = on_non_responder_escalated(
        _envelope(
            EventType.NON_RESPONDER_ESCALATED,
            {
                "deadline": "d",
                "form_link": "f",
                "invitees": [
                    {"email": "a@lge.com", "name": "A"},
                    {"email": "b@lge.com", "name": "B", "cc": ["lead@lge.com"]},
                ],
            },
        )
    )
    assert len(ids) == 2
    assert {row.recipient for row in _rows(session, "reminder_l3")} == {
        "a@lge.com",
        "b@lge.com",
    }


def test_non_responder_escalated_without_recipient_is_noop(session):
    assert on_non_responder_escalated(
        _envelope(EventType.NON_RESPONDER_ESCALATED, {"invitee_id": "IV001"})
    ) == []
    assert session.query(Notification).count() == 0


def test_participant_deferred_sends_defer_notice(session):
    """00_SHARED_CONTRACT 카탈로그: PARTICIPANT_DEFERRED (05 → 06)"""
    ids = on_participant_deferred(
        _envelope(
            EventType.PARTICIPANT_DEFERRED,
            {
                "next_round": "R2026-Q4-01",
                "recipients": [{"email": "a1@x.com", "name": "새한별"}],
            },
        )
    )
    assert len(ids) == 1
    row = _rows(session, "applicant_defer")[0]
    assert row.recipient == "a1@x.com"
    assert "새한별" in row.body
    assert "R2026-Q4-01" in row.body


def test_participant_deferred_accepts_deferred_recipients_key(session):
    ids = on_participant_deferred(
        _envelope(
            EventType.PARTICIPANT_DEFERRED,
            {"deferred_recipients": [{"email": "a2@x.com", "name": "홍길동"}]},
        )
    )
    assert len(ids) == 1
    assert _rows(session, "applicant_defer")[0].recipient == "a2@x.com"


def test_schedule_locked_sends_both_templates(session):
    ids = on_schedule_locked(
        _envelope(
            EventType.SCHEDULE_LOCKED,
            {
                "schedule_id": "S1",
                "lock_level": "LOCKED",
                "assignments_count": 42,
                "interviewers": [{"email": "iv1@lge.com", "name": "이지훈"}],
                "applicants": [
                    {"email": "a1@x.com", "name": "새한별", "day": "2일차", "hour": "10시"}
                ],
            },
        )
    )
    assert len(ids) == 2
    confirm = _rows(session, "interviewer_confirm")[0]
    assert confirm.recipient == "iv1@lge.com"
    assert "42" in confirm.body
    invite = _rows(session, "applicant_invite")[0]
    assert "2일차 10시" in invite.body


def test_repair_executed_routes_change_and_defer(session):
    ids = on_repair_executed(
        _envelope(
            EventType.REPAIR_EXECUTED,
            {
                "event_id": "E1",
                "plan_type": "REBOOK",
                "rebooked": 1,
                "deferred": 1,
                "rebooked_recipients": [
                    {"email": "a1@x.com", "name": "새한별", "new_slot": "3일차 14시"}
                ],
                "deferred_recipients": [{"email": "a2@x.com", "name": "홍길동"}],
                "next_round": "R2026-Q4-01",
            },
        )
    )
    assert len(ids) == 2
    change = _rows(session, "applicant_change")[0]
    assert "3일차 14시" in change.body
    defer = _rows(session, "applicant_defer")[0]
    assert "R2026-Q4-01" in defer.body


def test_repair_without_recipients_alerts_hr(session):
    on_repair_executed(
        _envelope(
            EventType.REPAIR_EXECUTED,
            {"event_id": "E1", "plan_type": "DEFER", "rebooked": 0, "deferred": 5},
        )
    )
    rows = _rows(session, "hr_alert_repair")
    assert len(rows) == 1
    assert rows[0].channel == "slack"
    assert "DEFER" in rows[0].body
    assert "5" in rows[0].body


def test_integrity_violated_alerts_hr_on_slack(session):
    on_integrity_violated(
        _envelope(
            EventType.INTEGRITY_VIOLATED,
            {
                "status": "VIOLATED",
                "duplicate_count": 3,
                "undistributed_count": 7,
                "issues": [],
            },
        )
    )
    rows = _rows(session, "hr_alert_integrity")
    assert len(rows) == 1
    assert rows[0].channel == "slack"
    assert rows[0].recipient == "hr-team@lge.com"
    assert "3" in rows[0].body and "7" in rows[0].body


def test_handler_with_no_recipients_is_noop(session):
    assert on_distribution_approved(_envelope(EventType.DISTRIBUTION_APPROVED, {})) == []
    assert session.query(Notification).count() == 0


def test_handler_skips_unrenderable_recipient(session):
    """컨텍스트가 부족한 수신자는 건너뛰고 나머지는 계속 발송한다."""
    ids = on_distribution_approved(
        _envelope(
            EventType.DISTRIBUTION_APPROVED,
            {
                "recipients": [
                    {"email": "bad@x.com"},  # name/day/hour 없음 → 렌더 실패
                    {"email": "good@x.com", "name": "새한별", "day": "1일차", "hour": "09시"},
                ]
            },
        )
    )
    assert len(ids) == 1
    rows = _rows(session, "applicant_invite")
    assert [row.recipient for row in rows] == ["good@x.com"]


def test_extract_recipients_accepts_plain_strings_and_dicts():
    assert extract_recipients({"recipients": ["a@x.com"]}) == [
        {"address": "a@x.com", "cc": [], "context": {}}
    ]
    entries = extract_recipients(
        {"invitees": [{"recipient": "b@x.com", "cc": ["c@x.com"], "name": "이"}]}
    )
    assert entries[0]["cc"] == ["c@x.com"]
    assert entries[0]["context"]["name"] == "이"
    assert extract_recipients({"recipients": [{"no_address": 1}, 42]}) == []
    assert extract_recipients({}) == []


def test_bus_dispatch_routes_to_handler(db, session):
    bus = get_event_bus()
    register_subscribers(bus)
    handled = bus.dispatch(
        _envelope(
            EventType.INTEGRITY_VIOLATED,
            {"status": "VIOLATED", "duplicate_count": 1, "undistributed_count": 0},
        )
    )
    assert handled == 1
    assert session.query(Notification).count() == 1


def test_bus_dispatch_unknown_type_is_noop(db):
    assert get_event_bus().dispatch({"event_type": "NOPE", "payload": {}}) == 0


def test_bus_dispatch_swallows_handler_errors(db):
    bus = get_event_bus()

    def boom(envelope):
        raise RuntimeError("핸들러 폭발")

    bus.subscribe("BOOM", boom)
    assert bus.dispatch({"event_type": "BOOM", "payload": {}}) == 1


def test_bus_publish_writes_to_redis_channel(db):
    bus = get_event_bus()
    pubsub = bus.client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(EVENT_CHANNEL)

    bus.publish("NOTIFICATION_SENT", payload={"x": 1}, round_id=ROUND, correlation_id="c")

    # 첫 get_message 는 subscribe ack 를 소비할 수 있으므로 몇 번 더 읽는다
    message = None
    for _ in range(5):
        message = pubsub.get_message(timeout=1)
        if message and message.get("type") == "message":
            break
    assert message is not None
    body = json.loads(message["data"])
    assert body["event_type"] == "NOTIFICATION_SENT"
    assert body["round_id"] == ROUND
    assert body["producer"] == "notification-hub"
    assert body["payload"] == {"x": 1}
    pubsub.close()


def test_inbound_event_endpoint_triggers_notifications(client, session):
    response = client.post(
        "/api/v1/notify/events/inbound",
        json=_envelope(
            EventType.INTEGRITY_VIOLATED,
            {"status": "VIOLATED", "duplicate_count": 2, "undistributed_count": 1},
        ),
    )
    assert response.status_code == 202
    assert response.json()["data"]["handlers"] == 1
    assert session.query(Notification).count() == 1


def test_event_catalog_endpoint(client):
    data = client.get("/api/v1/notify/events").json()["data"]
    assert set(data["subscribes"]) == set(SUBSCRIBED_EVENTS)
    assert set(data["registered"]) == set(SUBSCRIBED_EVENTS)
    assert "NOTIFICATION_OPENED" in data["publishes"]


def test_event_listener_thread_consumes_published_events(db, session):
    """리스너 스레드가 Pub/Sub 메시지를 구독 핸들러로 전달한다."""
    import time

    bus = get_event_bus()
    register_subscribers(bus)
    bus.start_listener()
    try:
        bus.client.publish(
            EVENT_CHANNEL,
            json.dumps(
                _envelope(
                    EventType.INTEGRITY_VIOLATED,
                    {"status": "VIOLATED", "duplicate_count": 9, "undistributed_count": 0},
                )
            ),
        )
        for _ in range(50):
            time.sleep(0.05)
            session.expire_all()
            if session.query(Notification).count():
                break
    finally:
        bus.stop_listener()

    session.expire_all()
    assert session.query(Notification).count() == 1


def test_listener_ignores_self_published_events(db, session):
    import time

    bus = get_event_bus()
    register_subscribers(bus)
    bus.start_listener()
    try:
        envelope = _envelope(EventType.INTEGRITY_VIOLATED, {"status": "VIOLATED"})
        envelope["producer"] = "notification-hub"
        bus.client.publish(EVENT_CHANNEL, json.dumps(envelope))
        time.sleep(0.4)
    finally:
        bus.stop_listener()

    session.expire_all()
    assert session.query(Notification).count() == 0
