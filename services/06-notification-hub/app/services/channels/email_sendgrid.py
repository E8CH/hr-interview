"""SendGrid 이메일 어댑터 (HTTP API)

USE_MOCK=true → 실제 호출 없이 요청 payload 를 outbox 에 기록.
"""
from __future__ import annotations

import httpx
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

SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"


class EmailSendGridAdapter:
    channel_id = "sendgrid"
    channel_type = "email"
    provider = "sendgrid"

    def build_payload(self, message: OutboundMessage) -> dict:
        personalization: dict = {"to": [{"email": message.recipient}]}
        if message.cc:
            personalization["cc"] = [{"email": addr} for addr in message.cc]
        return {
            "personalizations": [personalization],
            "from": {"email": message.config.get("mail_from", settings.mail_from)},
            "subject": message.subject or "(제목 없음)",
            "content": [{"type": "text/html", "value": message.body}],
            "custom_args": {"notification_id": message.notification_id},
        }

    def send(self, message: OutboundMessage) -> SendResult:
        payload = self.build_payload(message)
        if settings.use_mock:
            path = write_outbox(
                settings.outbox_dir, self.provider, message, dump_json(payload)
            )
            log.info("sendgrid_mock_sent", recipient=message.recipient, artifact=path)
            return SendResult(True, self.provider, "mock sendgrid", artifact=path)

        api_key = message.config.get("api_key") or ""
        if not api_key:
            raise ChannelSendError("SendGrid api_key 미설정")
        try:
            response = httpx.post(
                SENDGRID_URL,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            raise ChannelSendError(f"SendGrid 호출 실패: {exc}") from exc
        if response.status_code >= 400:
            raise ChannelSendError(
                f"SendGrid 응답 {response.status_code}: {response.text[:200]}"
            )
        return SendResult(True, self.provider, f"sendgrid {response.status_code}")
