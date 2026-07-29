"""이벤트 버스 어댑터 — Redis Pub/Sub (PoC 는 fakeredis 인메모리)

REDIS_URL 이 `fakeredis://` 이면 인메모리 서버를 사용한다.
발행 이력은 감사·테스트를 위해 메모리에 보관한다.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable

from app.config import settings

try:  # 감사 서비스(07) 포워딩 — fakeredis 는 프로세스 내부 전용이라 Pub/Sub이 닿지 않는다
    from shared.contracts.audit_sink import forward as forward_to_audit
except ImportError:  # pragma: no cover - shared/ 미탑재 환경 방어
    def forward_to_audit(envelope) -> None:  # type: ignore[misc]
        return None

log = logging.getLogger("repair-engine.eventbus")

CHANNEL_PREFIX = "hr.events"


def _make_client():
    url = settings.redis_url
    if url.startswith("fakeredis://"):
        import fakeredis
        return fakeredis.FakeStrictRedis(decode_responses=True)
    import redis
    return redis.Redis.from_url(url, decode_responses=True)


class EventBus:
    def __init__(self) -> None:
        self._client = None
        self._published: list[dict] = []
        self._handlers: dict[str, list[Callable[[dict], None]]] = {}

    @property
    def client(self):
        if self._client is None:
            self._client = _make_client()
        return self._client

    # --- 발행 ---
    def publish(self, envelope: dict) -> None:
        event_type = envelope.get("event_type", "UNKNOWN")
        channel = f"{CHANNEL_PREFIX}.{event_type}"
        body = json.dumps(envelope, ensure_ascii=False, default=str)
        try:
            self.client.publish(channel, body)
        except Exception as exc:  # PoC: 버스 장애가 재편성을 막지 않는다
            log.warning("event publish failed: %s (%s)", event_type, exc)
        self._published.append(envelope)
        forward_to_audit(envelope)
        log.info("event published: %s", event_type)
        # 같은 프로세스 내 구독자 (PoC 인메모리 디스패치)
        for handler in self._handlers.get(event_type, []):
            try:
                handler(envelope)
            except Exception as exc:
                log.warning("handler error for %s: %s", event_type, exc)

    # --- 구독 ---
    def subscribe(self, event_type: str, handler: Callable[[dict], None]) -> None:
        handlers = self._handlers.setdefault(event_type, [])
        if handler not in handlers:      # 중복 등록 방지 (startup + 테스트 fixture)
            handlers.append(handler)

    def dispatch(self, envelope: dict) -> None:
        """외부에서 수신한 이벤트를 내부 핸들러로 전달"""
        for handler in self._handlers.get(envelope.get("event_type", ""), []):
            handler(envelope)

    # --- 테스트·감사 보조 ---
    def published(self, event_type: str | None = None) -> list[dict]:
        if event_type is None:
            return list(self._published)
        return [e for e in self._published if e.get("event_type") == event_type]

    def reset(self) -> None:
        self._published.clear()


event_bus = EventBus()
