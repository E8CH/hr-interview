"""Service 06 (Notification Hub) 클라이언트

USE_MOCK=true (PoC 기본값) 이면 실제 HTTP 호출 대신 로컬 `./storage/outbox/` 에
발송 내역을 JSONL 로 적재한다. 통합 시 USE_MOCK=false 로 전환하면 동일 인터페이스로
Service 06 REST API 를 호출한다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog

from app.config import settings
from app.timeutil import utcnow

logger = structlog.get_logger(__name__)

OUTBOX_FILE = "outbox.jsonl"


@dataclass
class Message:
    """발송 메시지 (Service 06 페이로드)."""

    to: str
    subject: str
    body: str
    channel: str = "email"
    cc: list[str] = field(default_factory=list)
    kind: str = "invitation"  # 'invitation' | 'reminder'
    meta: dict[str, Any] = field(default_factory=dict)


class NotificationClient:
    def __init__(self, use_mock: bool | None = None, outbox_dir: Path | None = None) -> None:
        self.use_mock = settings.use_mock if use_mock is None else use_mock
        self.outbox_dir = outbox_dir or settings.outbox_dir
        self.base_url = settings.notification_url
        self.sent: list[Message] = []

    @property
    def outbox_path(self) -> Path:
        return self.outbox_dir / OUTBOX_FILE

    def send(self, message: Message) -> bool:
        """메시지 1건 발송. 성공 여부 반환."""
        if self.use_mock:
            return self._write_outbox(message)
        return self._post(message)

    def send_many(self, messages: list[Message]) -> int:
        """다건 발송 — 성공 건수 반환."""
        return sum(1 for m in messages if self.send(m))

    # --- mock ---
    def _write_outbox(self, message: Message) -> bool:
        record = asdict(message) | {"sent_at": utcnow().isoformat(), "mode": "mock"}
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        with self.outbox_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.sent.append(message)
        logger.info(
            "notification_sent_mock",
            to=message.to,
            kind=message.kind,
            channel=message.channel,
            cc=message.cc,
        )
        return True

    # --- 실제 호출 ---
    def _post(self, message: Message) -> bool:  # pragma: no cover - 통합 시 사용
        import httpx

        try:
            resp = httpx.post(
                f"{self.base_url}/api/v1/notifications",
                json=asdict(message),
                timeout=5.0,
            )
            resp.raise_for_status()
            self.sent.append(message)
            return True
        except Exception as exc:
            logger.error("notification_failed", to=message.to, error=str(exc))
            return False

    def read_outbox(self) -> list[dict]:
        """mock outbox 내용 조회 (테스트 · 완료 판정 로그 확인용)."""
        if not self.outbox_path.exists():
            return []
        with self.outbox_path.open(encoding="utf-8") as fp:
            return [json.loads(line) for line in fp if line.strip()]


_client: NotificationClient | None = None


def get_notification_client() -> NotificationClient:
    global _client
    if _client is None:
        _client = NotificationClient()
    return _client


def reset_notification_client() -> None:
    """테스트용."""
    global _client
    _client = None
