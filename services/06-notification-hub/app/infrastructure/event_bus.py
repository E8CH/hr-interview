"""이벤트 버스 — Redis Pub/Sub (PoC 는 fakeredis 인메모리)

00_SHARED_CONTRACT.md 의 EventEnvelope 봉투를 그대로 사용한다.
모든 이벤트는 단일 채널 `hr.events` 로 발행되고, 구독자는 event_type 으로 필터링한다.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

import structlog

from app.config import settings
from app.contracts.events import EventEnvelope

try:  # 감사 서비스(07) 포워딩 — fakeredis 는 프로세스 내부 전용이라 Pub/Sub이 닿지 않는다
    from shared.contracts.audit_sink import forward as forward_to_audit
except ImportError:  # pragma: no cover - shared/ 미탑재 환경 방어
    def forward_to_audit(envelope) -> None:  # type: ignore[misc]
        return None

log = structlog.get_logger(__name__)

EVENT_CHANNEL = "hr.events"

Handler = Callable[[dict], None]


def _make_redis_client(url: str):
    """fakeredis:// 스킴이면 인메모리 서버, 아니면 실제 Redis."""
    if url.startswith("fakeredis"):
        import fakeredis

        return fakeredis.FakeStrictRedis(decode_responses=True)
    import redis

    return redis.Redis.from_url(url, decode_responses=True)


class EventBus:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or settings.redis_url
        self.client = _make_redis_client(self.url)
        self._handlers: dict[str, list[Handler]] = {}
        self.published: list[dict] = []  # 감사·테스트용 인메모리 기록
        self._listener: threading.Thread | None = None
        self._stop = threading.Event()

    # --- 발행 ---
    def publish(
        self,
        event_type: str,
        *,
        payload: dict[str, Any],
        round_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict:
        envelope = EventEnvelope(
            event_type=event_type,
            round_id=round_id or "",
            producer=settings.PRODUCER,
            correlation_id=correlation_id or "",
            payload=payload,
        )
        body = json.loads(envelope.model_dump_json())
        self.published.append(body)
        try:
            self.client.publish(EVENT_CHANNEL, json.dumps(body))
        except Exception as exc:  # pragma: no cover - PoC 에서는 버스 장애를 무시
            log.warning("event_publish_failed", event_type=event_type, error=str(exc))
        forward_to_audit(body)
        log.info("event_published", event_type=event_type, event_id=body["event_id"])
        return body

    # --- 구독 ---
    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def dispatch(self, envelope: dict) -> int:
        """봉투를 등록된 핸들러들에게 전달. 처리된 핸들러 수를 반환."""
        event_type = envelope.get("event_type", "")
        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                handler(envelope)
            except Exception as exc:
                log.error(
                    "event_handler_failed",
                    event_type=event_type,
                    handler=getattr(handler, "__name__", str(handler)),
                    error=str(exc),
                )
        return len(handlers)

    def subscribed_types(self) -> list[str]:
        return sorted(self._handlers)

    # --- 리스너 스레드 ---
    def start_listener(self) -> None:
        if self._listener is not None:
            return
        self._stop.clear()
        pubsub = self.client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(EVENT_CHANNEL)

        def _run() -> None:
            while not self._stop.is_set():
                try:
                    message = pubsub.get_message(timeout=0.5)
                except Exception:  # pragma: no cover
                    break
                if not message:
                    continue
                data = message.get("data")
                if not data:
                    continue
                try:
                    envelope = json.loads(data)
                except (TypeError, ValueError):  # pragma: no cover
                    continue
                if envelope.get("producer") == settings.PRODUCER:
                    continue  # 자기 발행 이벤트는 재처리하지 않음
                self.dispatch(envelope)
            try:  # pragma: no cover
                pubsub.close()
            except Exception:
                pass

        self._listener = threading.Thread(target=_run, name="event-listener", daemon=True)
        self._listener.start()
        log.info("event_listener_started", types=self.subscribed_types())

    def stop_listener(self) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.join(timeout=2)
            self._listener = None


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_event_bus() -> EventBus:
    """테스트용 — 버스를 새로 만든다."""
    global _bus
    if _bus is not None:
        _bus.stop_listener()
    _bus = EventBus()
    return _bus
