"""리마인더 발송 오케스트레이션 — DB · 알림 · 이벤트 반영"""
from datetime import timedelta

import pytest

from app.domain.reminder import Reminder
from app.events import EventType
from app.services import reminder_service, response_service
from app.timeutil import utcnow
from tests.conftest import shift_sent_at


def test_cycle_sends_nothing_before_24h(db, created_request, bus):
    assert reminder_service.run_reminder_cycle(db) == []
    assert bus.history(EventType.REMINDER_SENT) == []


def test_cycle_sends_level1_at_24h(db, created_request, bus, notifier):
    shift_sent_at(db, created_request, 25)
    sent = reminder_service.run_reminder_cycle(db)

    assert len(sent) == 3
    assert {r.level for r in sent} == {1}
    assert all(r.cc_supervisor is False for r in sent)

    events = bus.history(EventType.REMINDER_SENT)
    assert len(events) == 3
    assert events[0].payload["level"] == 1
    assert events[0].payload["channel"] == "email"

    reminders = [m for m in notifier.read_outbox() if m["kind"] == "reminder"]
    assert len(reminders) == 3
    assert "정중" in reminders[0]["subject"]


def test_cycle_is_idempotent_within_level(db, created_request):
    shift_sent_at(db, created_request, 25)
    assert len(reminder_service.run_reminder_cycle(db)) == 3
    assert reminder_service.run_reminder_cycle(db) == []


def test_responder_excluded_from_cycle(db, created_request, valid_payload):
    """명세 완료 판정: 이미 회신한 사람에겐 리마인더가 가지 않는다."""
    responder = created_request.invitees[0]
    response_service.submit_response(db, responder, valid_payload)

    shift_sent_at(db, created_request, 25)
    sent = reminder_service.run_reminder_cycle(db)

    assert len(sent) == 2
    assert responder.invitee_id not in {r.invitee_id for r in sent}


def test_level_progression_24_48_68(db, created_request, bus):
    shift_sent_at(db, created_request, 25)
    assert {r.level for r in reminder_service.run_reminder_cycle(db)} == {1}

    shift_sent_at(db, created_request, 49)
    assert {r.level for r in reminder_service.run_reminder_cycle(db)} == {2}

    shift_sent_at(db, created_request, 69)
    assert {r.level for r in reminder_service.run_reminder_cycle(db)} == {3}

    assert len(bus.history(EventType.REMINDER_SENT)) == 9
    assert db.query(Reminder).count() == 9


def test_level3_ccs_supervisor_and_escalates(db, created_request, bus, notifier):
    shift_sent_at(db, created_request, 69)
    sent = reminder_service.run_reminder_cycle(db)

    assert {r.level for r in sent} == {3}
    assert all(r.cc_supervisor is True for r in sent)

    reminders = [m for m in notifier.read_outbox() if m["kind"] == "reminder"]
    assert reminders[0]["cc"] == [created_request.invitees[0].dept_leader_email]
    assert "최종알림" in reminders[0]["subject"]

    escalations = bus.history(EventType.NON_RESPONDER_ESCALATED)
    assert len(escalations) == 3
    payload = escalations[0].payload
    assert payload["supervisor_email"] == created_request.invitees[0].dept_leader_email
    assert payload["hours_since_sent"] >= 68
    assert escalations[0].round_id == created_request.round_id


def test_no_reminder_after_level3(db, created_request):
    shift_sent_at(db, created_request, 69)
    assert len(reminder_service.run_reminder_cycle(db)) == 3

    shift_sent_at(db, created_request, 500)
    assert reminder_service.run_reminder_cycle(db) == []


def test_closed_request_excluded(db, created_request):
    from app.services.request_service import close_request

    shift_sent_at(db, created_request, 69)
    close_request(db, created_request)
    assert reminder_service.run_reminder_cycle(db) == []


def test_backlog_collapses_to_single_highest_level(db, created_request):
    """스케줄러가 멈춰 있던 사이 1·2·3 이 모두 도래해도 1건만 발송."""
    shift_sent_at(db, created_request, 100)
    sent = reminder_service.run_reminder_cycle(db)
    assert len(sent) == 3
    assert {r.level for r in sent} == {3}


def test_correlation_id_shared_across_events(db, created_request, bus):
    shift_sent_at(db, created_request, 69)
    reminder_service.run_reminder_cycle(db)

    ids = {e.correlation_id for e in bus.history()}
    assert ids == {created_request.correlation_id}


def test_events_follow_shared_envelope(db, created_request, bus):
    shift_sent_at(db, created_request, 25)
    reminder_service.run_reminder_cycle(db)

    for event in bus.history():
        assert event.event_id
        assert event.timestamp
        assert event.round_id == created_request.round_id
        assert event.producer == "response-collector"
        assert event.correlation_id
        assert isinstance(event.payload, dict)


def test_manual_trigger(db, first_invitee, bus):
    reminder, reason = reminder_service.trigger_manual(db, first_invitee, level=2)
    assert reminder is not None
    assert reminder.level == 2
    assert "수동 발송" in reason
    assert len(bus.history(EventType.REMINDER_SENT)) == 1


def test_manual_trigger_skips_responder(db, first_invitee, valid_payload):
    response_service.submit_response(db, first_invitee, valid_payload)
    reminder, reason = reminder_service.trigger_manual(db, first_invitee, level=2)
    assert reminder is None
    assert reason == "이미 회신함"


def test_manual_trigger_force_overrides(db, first_invitee, valid_payload):
    response_service.submit_response(db, first_invitee, valid_payload)
    reminder, _ = reminder_service.trigger_manual(db, first_invitee, level=3, force=True)
    assert reminder is not None
    assert reminder.cc_supervisor is True


def test_manual_trigger_skips_already_sent_level(db, first_invitee):
    reminder_service.trigger_manual(db, first_invitee, level=2)
    reminder, reason = reminder_service.trigger_manual(db, first_invitee, level=2)
    assert reminder is None
    assert "이미 Level 2" in reason


def test_reminder_sent_at_within_5min_of_due(db, created_request):
    """명세 성공 기준: 리마인더 스케줄 정확도 ±5분."""
    from app.services.reminder_engine import reminder_due_at

    shift_sent_at(db, created_request, 24.01)
    sent = reminder_service.run_reminder_cycle(db)
    due = reminder_due_at(created_request.sent_at, 1)
    assert abs((sent[0].sent_at - due).total_seconds()) <= 300


def test_scheduler_job_runs_without_error(db, created_request):
    from app.infrastructure.scheduler import reminder_job

    shift_sent_at(db, created_request, 25)
    assert reminder_job() == 3


def test_unsent_request_never_reminded(db, created_request):
    created_request.sent_at = None
    db.commit()
    assert reminder_service.run_reminder_cycle(db, now=utcnow() + timedelta(days=10)) == []
