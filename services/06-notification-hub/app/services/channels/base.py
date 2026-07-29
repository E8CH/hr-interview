"""채널 어댑터 공통 인터페이스

자격증명은 어댑터 내부(또는 channels 테이블 config)에만 존재한다.
다른 서비스는 물론, API 계층도 SMTP/Webhook 정보를 알 필요가 없다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


class ChannelSendError(Exception):
    """어댑터 발송 실패 — dispatcher 가 잡아 재시도로 연결한다."""


class ChannelDisabledError(ChannelSendError):
    """사용 가능한 채널이 없음."""


@dataclass
class OutboundMessage:
    notification_id: str
    recipient: str
    subject: str | None = None
    body: str = ""
    cc: list[str] = field(default_factory=list)
    channel_type: str = "email"
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class SendResult:
    ok: bool
    provider: str
    detail: str = ""
    artifact: str | None = None  # PoC: 로컬에 남긴 파일 경로


class ChannelAdapter(Protocol):
    channel_id: str
    channel_type: str

    def send(self, message: OutboundMessage) -> SendResult:  # pragma: no cover
        ...


def _safe(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in value)[:80]


def write_outbox(
    outbox_dir: Path, provider: str, message: OutboundMessage, payload: str
) -> str:
    """PoC 모드 산출물 — 발송 대신 로컬 파일로 남긴다."""
    target = outbox_dir / provider
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = target / f"{stamp}_{_safe(message.recipient)}_{message.notification_id}.txt"
    path.write_text(payload, encoding="utf-8")
    return str(path)


def dump_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
