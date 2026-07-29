"""발송 파이프라인 — 이메일/Slack/SMS 어댑터, 큐 적재, 이벤트 발행"""
from __future__ import annotations

import pytest

from app.domain.models import Notification
from app.events import NOTIFICATION_SENT
from app.infrastructure.event_bus import get_event_bus
from app.services.channels import (
    EmailSendGridAdapter,
    EmailSmtpAdapter,
    OutboundMessage,
    SlackWebhookAdapter,
    SmsStubAdapter,
)
from app.services.channels.base import ChannelSendError
from app.services.dispatcher import deliver, enqueue, resolve_channel


def _msg(**kwargs) -> OutboundMessage:
    base = dict(
        notification_id="n-1",
        recipient="iv1@lge.com",
        subject="제목",
        body="<p>본문</p>",
    )
    base.update(kwargs)
    return OutboundMessage(**base)


def test_email_send_via_smtp_mock(session, db, invite_context):
    """로컬 SMTP mock 으로 발송 성공 → status=sent, NOTIFICATION_SENT 발행"""
    notification = enqueue(
        session,
        template_id="invite",
        channel="email",
        recipient="iv1@lge.com",
        context=invite_context,
        cc=["backup@lge.com"],
        correlation_id="R2026-Q3-01/invitee-abc",
        round_id="R2026-Q3-01",
    )
    session.commit()
    assert notification.status == "queued"
    assert notification.attempt_count == 0

    assert deliver(session, notification) is True
    session.commit()

    assert notification.status == "sent"
    assert notification.sent_at is not None
    assert notification.attempt_count == 1
    assert notification.error_message is None

    events = [e for e in get_event_bus().published if e["event_type"] == NOTIFICATION_SENT]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["notification_id"] == notification.notification_id
    assert payload["recipient"] == "iv1@lge.com"
    assert payload["channel"] == "email"
    assert events[0]["producer"] == "notification-hub"
    assert events[0]["correlation_id"] == "R2026-Q3-01/invitee-abc"

    # PoC: 실제 발송 대신 outbox 파일로 남는다
    outbox = db["storage"] / "outbox" / "smtp"
    files = list(outbox.glob("*.txt"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "iv1@lge.com" in content
    assert "backup@lge.com" in content
    assert "이지훈" in content or "=?utf-8?" in content


def test_enqueue_renders_and_injects_pixel(session, invite_context):
    notification = enqueue(
        session,
        template_id="invite",
        channel="email",
        recipient="iv1@lge.com",
        context=invite_context,
    )
    session.commit()
    assert "이지훈" in notification.body
    assert f"track/open/{notification.notification_id}.png" in notification.body


def test_enqueue_unknown_template_404(session):
    from app.responses import NotFoundError

    with pytest.raises(NotFoundError):
        enqueue(session, template_id="nope", channel="email", recipient="a@b.com")


def test_enqueue_missing_context_variable_is_validation_error(session):
    from app.responses import ValidationFailed

    with pytest.raises(ValidationFailed):
        enqueue(session, template_id="invite", channel="email", recipient="a@b.com")


def test_enqueue_rejects_unknown_channel_and_empty_recipient(session, invite_context):
    from app.responses import ValidationFailed

    with pytest.raises(ValidationFailed):
        enqueue(session, template_id="invite", channel="fax", recipient="a@b.com")
    with pytest.raises(ValidationFailed):
        enqueue(
            session,
            template_id="invite",
            channel="email",
            recipient="  ",
            context=invite_context,
        )


def test_slack_channel_delivers(session, db):
    notification = enqueue(
        session,
        template_id="hr_alert_integrity",
        channel="slack",
        recipient="#hr-alerts",
        context={"round_id": "R2026-Q3-01", "duplicate_count": 3},
    )
    session.commit()
    assert deliver(session, notification) is True
    assert notification.status == "sent"
    # slack 은 픽셀을 넣지 않는다
    assert "<img" not in (notification.body or "")
    assert list((db["storage"] / "outbox" / "slack").glob("*.txt"))


def test_sms_channel_is_stub(session, db):
    notification = enqueue(
        session,
        template_id="applicant_defer",
        channel="sms",
        recipient="010-1234-5678",
        context={"name": "새한별"},
    )
    session.commit()
    assert deliver(session, notification) is True
    assert list((db["storage"] / "outbox" / "sms").glob("*.txt"))


def test_resolve_channel_prefers_enabled_row(session):
    adapter, config, channel_id = resolve_channel(session, "email")
    assert channel_id == "gmail_smtp"  # sendgrid 는 seed 에서 disabled
    assert config["adapter"] == "smtp"
    assert adapter.channel_type == "email"


def test_resolve_channel_all_disabled_raises(session):
    from app.domain.models import Channel
    from app.services.channels import ChannelDisabledError

    for row in session.query(Channel).filter(Channel.channel_type == "email"):
        row.enabled = False
    session.commit()

    with pytest.raises(ChannelDisabledError):
        resolve_channel(session, "email")


def test_resolve_channel_unknown_type(session):
    from app.responses import ValidationFailed

    with pytest.raises(ValidationFailed):
        resolve_channel(session, "carrier-pigeon")


def test_resolve_channel_falls_back_when_table_empty(session):
    from app.domain.models import Channel

    session.query(Channel).delete()
    session.commit()
    adapter, config, channel_id = resolve_channel(session, "email")
    assert channel_id == "gmail_smtp"
    assert config == {}


def test_deliver_is_idempotent_for_sent(session, invite_context):
    notification = enqueue(
        session,
        template_id="invite",
        channel="email",
        recipient="iv1@lge.com",
        context=invite_context,
    )
    session.commit()
    deliver(session, notification)
    attempts = notification.attempt_count
    assert deliver(session, notification) is True
    assert notification.attempt_count == attempts


def test_deliver_by_id_missing_returns_false(session):
    from app.services.dispatcher import deliver_by_id

    assert deliver_by_id(session, "does-not-exist") is False


# --- 어댑터 단위 테스트 ---
def test_smtp_adapter_builds_message(db):
    mail = EmailSmtpAdapter().build_message(_msg(cc=["c@lge.com"]))
    assert mail["To"] == "iv1@lge.com"
    assert mail["Cc"] == "c@lge.com"
    assert mail["X-Notification-Id"] == "n-1"


def test_smtp_adapter_real_send_failure_raises(db, monkeypatch):
    monkeypatch.setenv("USE_MOCK", "false")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "1")  # 닫힌 포트
    with pytest.raises(ChannelSendError):
        EmailSmtpAdapter().send(_msg())


def test_sendgrid_adapter_payload_and_mock(db):
    adapter = EmailSendGridAdapter()
    payload = adapter.build_payload(_msg(cc=["c@lge.com"]))
    assert payload["personalizations"][0]["to"][0]["email"] == "iv1@lge.com"
    assert payload["personalizations"][0]["cc"][0]["email"] == "c@lge.com"
    assert adapter.send(_msg()).ok is True


def test_sendgrid_requires_api_key_when_live(db, monkeypatch):
    monkeypatch.setenv("USE_MOCK", "false")
    with pytest.raises(ChannelSendError):
        EmailSendGridAdapter().send(_msg())


def test_slack_adapter_payload(db):
    adapter = SlackWebhookAdapter()
    payload = adapter.build_payload(_msg(recipient="#hr", body="본문", subject="제목"))
    assert payload["channel"] == "#hr"
    assert payload["text"].startswith("*제목*")
    assert adapter.send(_msg(recipient="#hr")).ok is True


def test_slack_requires_webhook_when_live(db, monkeypatch):
    monkeypatch.setenv("USE_MOCK", "false")
    with pytest.raises(ChannelSendError):
        SlackWebhookAdapter().send(_msg(recipient="#hr"))


def test_sms_adapter_truncates_and_validates(db):
    adapter = SmsStubAdapter()
    payload = adapter.build_payload(_msg(recipient="01012345678", body="가" * 3000))
    assert len(payload["text"]) == 2000
    assert payload["text"].endswith("...")
    with pytest.raises(ChannelSendError):
        adapter.send(_msg(recipient="   "))


def test_notification_to_dict_roundtrip(session, invite_context):
    notification = enqueue(
        session,
        template_id="invite",
        channel="email",
        recipient="iv1@lge.com",
        context=invite_context,
        correlation_id="cid",
    )
    session.commit()
    data = notification.to_dict()
    assert data["correlation_id"] == "cid"
    assert data["status"] == "queued"
    assert isinstance(data["cc"], list)
    assert session.get(Notification, data["notification_id"]) is notification
