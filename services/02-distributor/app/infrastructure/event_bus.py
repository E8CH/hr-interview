"""이벤트 버스 어댑터 — PoC는 fakeredis 인메모리 Pub/Sub.

REDIS_URL=fakeredis:// 이면 프로세스 내 FakeRedis 서버를 공유한다.
그 외 URL이면 실제 redis-py 클라이언트를 사용한다.
채널명 = event_type (00_SHARED_CONTRACT 이벤트 카탈로그 이름 그대로).
"""
from __future__ import annotations

import json
import time
from typing import Callable

from contracts.events import EventEnvelope

from app.config import settings

try:  # 감사 서비스(07) 포워딩 — fakeredis 는 프로세스 내부 전용이라 Pub/Sub이 닿지 않는다
    from contracts.audit_sink import forward as forward_to_audit
except ImportError:  # pragma: no cover - shared/ 미탑재 환경 방어
    def forward_to_audit(envelope) -> None:  # type: ignore[misc]
        return None

Handler = Callable[[EventEnvelope], None]


def _build_client():
    url = settings.redis_url
    if url.startswith("fakeredis"):
        import fakeredis

        global _FAKE_SERVER
        if _FAKE_SERVER is None:
            _FAKE_SERVER = fakeredis.FakeServer()
        return fakeredis.FakeRedis(server=_FAKE_SERVER, decode_responses=True)
    import redis

    return redis.Redis.from_url(url, decode_responses=True)


_FAKE_SERVER = None


class EventBus:
    """발행 + 로컬 동기 구독 디스패치."""

    def __init__(self) -> None:
        self._client = _build_client()
        self._handlers: dict[str, list[Handler]] = {}
        self._published: list[EventEnvelope] = []

    # --- 발행 ---
    def publish(self, envelope: EventEnvelope) -> EventEnvelope:
        payload = envelope.model_dump_json()
        self._client.publish(envelope.event_type, payload)
        self._published.append(envelope)
        forward_to_audit(envelope)
        for handler in self._handlers.get(envelope.event_type, []):
            handler(envelope)
        return envelope

    # --- 구독 ---
    def subscribe(self, event_type: str, handler: Handler) -> None:
        handlers = self._handlers.setdefault(event_type, [])
        if handler not in handlers:  # 재등록 시 중복 디스패치 방지
            handlers.append(handler)

    def dispatch(self, raw: str | dict) -> EventEnvelope:
        """외부에서 들어온 원시 메시지를 봉투로 파싱해 로컬 핸들러에 전달."""
        data = json.loads(raw) if isinstance(raw, str) else raw
        envelope = EventEnvelope(**data)
        for handler in self._handlers.get(envelope.event_type, []):
            handler(envelope)
        return envelope

    def listener(self, *event_types: str):
        """다른 서비스가 수신 가능한지 확인하기 위한 pubsub 구독자."""
        pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(*event_types)
        return pubsub

    # --- 테스트/관측 ---
    @property
    def published(self) -> list[EventEnvelope]:
        return list(self._published)

    def published_of(self, event_type: str) -> list[EventEnvelope]:
        return [e for e in self._published if e.event_type == event_type]

    def clear(self) -> None:
        self._published.clear()


def receive_message(pubsub, timeout: float = 1.0) -> dict | None:
    """pubsub에서 실제 메시지 1건을 기다린다.

    redis-py의 `get_message()`는 subscribe 확인 메시지를 소비하면 None을 돌려주므로
    데드라인까지 반복해서 읽어야 한다.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = pubsub.get_message(timeout=0.05)
        if message and message.get("type") == "message":
            return message
    return None


_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_bus() -> None:
    """테스트 격리용."""
    global _bus
    _bus = None
