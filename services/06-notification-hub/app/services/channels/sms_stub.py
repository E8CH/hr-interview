"""SMS 어댑터 — PoC 단계에서는 stub (명세 허용)

실제 게이트웨이 연동 시 send() 내부만 교체하면 된다.
"""
from __future__ import annotations

import structlog

from app.config import settings
from app.services.channels.base import (
    ChannelSendError,
    OutboundMessage,
    SendResult,
    dump_json,
    write_outbox,
)

log = structlog.get_logger(__name__)

MAX_SMS_LEN = 2000


class SmsStubAdapter:
    channel_id = "sms_gateway"
    channel_type = "sms"
    provider = "sms"

    def build_payload(self, message: OutboundMessage) -> dict:
        text = message.body.strip()
        if len(text) > MAX_SMS_LEN:
            text = text[: MAX_SMS_LEN - 3] + "..."
        return {
            "to": message.recipient,
            "text": text,
            "notification_id": message.notification_id,
        }

    def send(self, message: OutboundMessage) -> SendResult:
        if not message.recipient.strip():
            raise ChannelSendError("SMS 수신번호 없음")
        payload = self.build_payload(message)
        path = write_outbox(
            settings.outbox_dir, self.provider, message, dump_json(payload)
        )
        log.info("sms_stub_sent", recipient=message.recipient, artifact=path)
        return SendResult(True, self.provider, "sms stub", artifact=path)
