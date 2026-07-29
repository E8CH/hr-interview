"""재시도 정책 — 3회 시도 후 dead letter 큐"""
from __future__ import annotations

import pytest

from app.domain.models import DeadLetter, utcnow
from app.events import NOTIFICATION_FAILED, NOTIFICATION_SENT
from app.infrastructure.event_bus import get_event_bus
from app.infrastructure.queue import list_dead_letters, pull_due, queued_count
from app.services.channels import ADAPTERS_BY_NAME, OutboundMessage, SendResult
from app.services.channels.base import ChannelSendError
from app.services.dispatcher import deliver, enqueue, process_due


class FailingAdapter:
    channel_id = "gmail_smtp"
    channel_type = "email"
    provider = "smtp"

    def __init__(self, message: str = "SMTP 연결 거부") -> None:
        self.calls = 0
        self.message = message

    def send(self, message: OutboundMessage) -> SendResult:
        self.calls += 1
        raise ChannelSendError(self.message)


class FlakyAdapter(FailingAdapter):
    """N번째 시도부터 성공."""

    def __init__(self, succeed_on: int) -> None:
        super().__init__()
        self.succeed_on = succeed_on

    def send(self, message: OutboundMessage) -> SendResult:
        self.calls += 1
        if self.calls < self.succeed_on:
            raise ChannelSendError("일시 장애")
        return SendResult(True, "smtp", "recovered")


@pytest.fixture
def queued(session, invite_context):
    notification = enqueue(
        session,
        template_id="invite",
        channel="email",
        recipient="iv1@lge.com",
        context=invite_context,
        correlation_id="R2026-Q3-01/invitee-abc",
        round_id="R2026-Q3-01",
    )
    session.commit()
    return notification


def test_retry_three_times_then_dead_letter(session, queued, monkeypatch):
    adapter = FailingAdapter()
    monkeypatch.setitem(ADAPTERS_BY_NAME, "smtp", adapter)

    # 1회차 실패 → 여전히 queued
    assert deliver(session, queued) is False
    assert queued.status == "queued"
    assert queued.attempt_count == 1

    # 2회차 실패 → 여전히 queued
    assert deliver(session, queued) is False
    assert queued.status == "queued"
    assert queued.attempt_count == 2

    # 3회차 실패 → failed + dead letter
    assert deliver(session, queued) is False
    session.commit()
    assert queued.status == "failed"
    assert queued.attempt_count == 3
    assert "SMTP 연결 거부" in queued.error_message
    assert adapter.calls == 3

    dead = list_dead_letters(session)
    assert len(dead) == 1
    assert dead[0].notification_id == queued.notification_id
    assert dead[0].attempt_count == 3
    assert dead[0].correlation_id == "R2026-Q3-01/invitee-abc"
    assert dead[0].snapshot["recipient"] == "iv1@lge.com"

    failed_events = [
        e for e in get_event_bus().published if e["event_type"] == NOTIFICATION_FAILED
    ]
    assert len(failed_events) == 1
    assert failed_events[0]["payload"]["dead_letter"] is True
    assert failed_events[0]["payload"]["attempt_count"] == 3
    assert not [
        e for e in get_event_bus().published if e["event_type"] == NOTIFICATION_SENT
    ]


def test_dead_letter_notification_is_not_retried(session, queued, monkeypatch):
    adapter = FailingAdapter()
    monkeypatch.setitem(ADAPTERS_BY_NAME, "smtp", adapter)
    for _ in range(3):
        deliver(session, queued)
    session.commit()

    assert pull_due(session) == []  # failed 는 큐에서 빠진다
    assert queued_count(session) == 0
    assert process_due(session) == {"processed": 0, "sent": 0, "failed": 0}
    assert adapter.calls == 3


def test_dead_letter_push_is_idempotent(session, queued):
    from app.infrastructure.queue import push_dead_letter

    first = push_dead_letter(session, queued, "이유1")
    second = push_dead_letter(session, queued, "이유2")
    assert first.dead_letter_id == second.dead_letter_id
    assert session.query(DeadLetter).count() == 1


def test_retry_recovers_before_limit(session, queued, monkeypatch):
    monkeypatch.setitem(ADAPTERS_BY_NAME, "smtp", FlakyAdapter(succeed_on=3))

    assert deliver(session, queued) is False
    assert deliver(session, queued) is False
    assert deliver(session, queued) is True
    session.commit()

    assert queued.status == "sent"
    assert queued.attempt_count == 3
    assert queued.error_message is None
    assert session.query(DeadLetter).count() == 0


def test_backoff_schedule_pushes_next_attempt(session, queued, monkeypatch):
    """RETRY_DELAYS 를 실제 값(0/30/300초)으로 두면 백오프가 잡힌다."""
    monkeypatch.setenv("RETRY_DELAYS", "0,30,300")
    monkeypatch.setitem(ADAPTERS_BY_NAME, "smtp", FailingAdapter())

    deliver(session, queued)  # 1회차 실패 → 30초 뒤 재시도
    session.commit()
    delta = (queued.next_attempt_at - utcnow()).total_seconds()
    assert 25 < delta <= 30
    assert pull_due(session) == []  # 아직 시각이 안 됨

    deliver(session, queued)  # 2회차 실패 → 5분 뒤
    session.commit()
    delta = (queued.next_attempt_at - utcnow()).total_seconds()
    assert 290 < delta <= 300


def test_process_due_handles_mixed_results(session, invite_context, monkeypatch):
    for i in range(3):
        enqueue(
            session,
            template_id="invite",
            channel="email",
            recipient=f"iv{i}@lge.com",
            context=invite_context,
        )
    session.commit()

    monkeypatch.setitem(ADAPTERS_BY_NAME, "smtp", FailingAdapter())
    result = process_due(session)
    assert result == {"processed": 3, "sent": 0, "failed": 3}
    assert queued_count(session) == 3  # 아직 재시도 여유가 있음

    monkeypatch.setitem(ADAPTERS_BY_NAME, "smtp", ADAPTERS_BY_NAME["sendgrid"])
    result = process_due(session)
    assert result == {"processed": 3, "sent": 3, "failed": 0}
    assert queued_count(session) == 0


def test_worker_run_once_processes_queue(session, queued):
    from app.services.retry_worker import run_once

    result = run_once()
    assert result["processed"] == 1
    assert result["sent"] == 1
    session.refresh(queued)
    assert queued.status == "sent"


@pytest.mark.asyncio
async def test_retry_worker_loop_drains_queue(session, queued):
    import asyncio

    from app.services.retry_worker import RetryWorker

    worker = RetryWorker(interval=0.05)
    worker.start()
    for _ in range(40):
        await asyncio.sleep(0.05)
        session.expire_all()
        if session.get(type(queued), queued.notification_id).status == "sent":
            break
    await worker.stop()

    session.expire_all()
    assert session.get(type(queued), queued.notification_id).status == "sent"
    assert worker.cycles >= 1


def test_dead_letters_endpoint(client, session, invite_context, monkeypatch):
    monkeypatch.setitem(ADAPTERS_BY_NAME, "smtp", FailingAdapter())
    notification = enqueue(
        session,
        template_id="invite",
        channel="email",
        recipient="dead@lge.com",
        context=invite_context,
    )
    session.commit()
    for _ in range(3):
        deliver(session, notification)
    session.commit()

    body = client.get("/api/v1/notify/dead-letters").json()
    assert body["error"] is None
    assert body["data"]["count"] == 1
    assert body["data"]["items"][0]["recipient"] == "dead@lge.com"
