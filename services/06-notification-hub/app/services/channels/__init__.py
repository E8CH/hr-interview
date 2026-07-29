"""채널 어댑터 레지스트리 — 채널 라우팅의 유일한 진입점"""
from __future__ import annotations

from app.services.channels.base import (
    ChannelAdapter,
    ChannelDisabledError,
    ChannelSendError,
    OutboundMessage,
    SendResult,
)
from app.services.channels.email_sendgrid import EmailSendGridAdapter
from app.services.channels.email_smtp import EmailSmtpAdapter
from app.services.channels.slack_webhook import SlackWebhookAdapter
from app.services.channels.sms_stub import SmsStubAdapter

# channels.config["adapter"] 값 → 어댑터 인스턴스
ADAPTERS_BY_NAME: dict[str, ChannelAdapter] = {
    "smtp": EmailSmtpAdapter(),
    "sendgrid": EmailSendGridAdapter(),
    "slack": SlackWebhookAdapter(),
    "sms": SmsStubAdapter(),
}

# channel_type 기본 어댑터 (channels 테이블에 항목이 없을 때의 폴백)
DEFAULT_BY_TYPE: dict[str, ChannelAdapter] = {
    "email": ADAPTERS_BY_NAME["smtp"],
    "slack": ADAPTERS_BY_NAME["slack"],
    "sms": ADAPTERS_BY_NAME["sms"],
}

SUPPORTED_TYPES = tuple(DEFAULT_BY_TYPE)


def get_adapter(name: str) -> ChannelAdapter | None:
    return ADAPTERS_BY_NAME.get(name)


__all__ = [
    "ADAPTERS_BY_NAME",
    "DEFAULT_BY_TYPE",
    "SUPPORTED_TYPES",
    "ChannelAdapter",
    "ChannelDisabledError",
    "ChannelSendError",
    "EmailSendGridAdapter",
    "EmailSmtpAdapter",
    "OutboundMessage",
    "SendResult",
    "SlackWebhookAdapter",
    "SmsStubAdapter",
    "get_adapter",
]
