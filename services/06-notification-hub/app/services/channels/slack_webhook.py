"""Slack Incoming Webhook 어댑터"""
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


class SlackWebhookAdapter:
    channel_id = "slack_hr"
    channel_type = "slack"
    provider = "slack"

    def build_payload(self, message: OutboundMessage) -> dict:
        text = message.body
        if message.subject:
            text = f"*{message.subject}*\n{text}"
        payload: dict = {"text": text}
        channel = message.recipient
        if channel.startswith("#") or channel.startswith("@"):
            payload["channel"] = channel
        return payload

    def send(self, message: OutboundMessage) -> SendResult:
        payload = self.build_payload(message)
        if settings.use_mock:
            path = write_outbox(
                settings.outbox_dir, self.provider, message, dump_json(payload)
            )
            log.info("slack_mock_sent", recipient=message.recipient, artifact=path)
            return SendResult(True, self.provider, "mock slack", artifact=path)

        webhook_url = message.config.get("webhook_url")
        if not webhook_url:
            raise ChannelSendError("Slack webhook_url 미설정")
        try:
            response = httpx.post(webhook_url, json=payload, timeout=10.0)
        except httpx.HTTPError as exc:
            raise ChannelSendError(f"Slack webhook 호출 실패: {exc}") from exc
        if response.status_code >= 400:
            raise ChannelSendError(
                f"Slack 응답 {response.status_code}: {response.text[:200]}"
            )
        return SendResult(True, self.provider, f"slack {response.status_code}")
