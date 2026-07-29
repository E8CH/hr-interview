"""이벤트 버스 — Redis Pub/Sub (PoC 는 fakeredis 인메모리)

- 단일 채널 `hr-events` 에 봉투 JSON 을 발행하고, 구독자는 `event_type` 으로 디스패치한다.
- 발행 이력은 인메모리 링버퍼에 남겨 `/metrics` 와 테스트에서 확인한다.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Callable

import structlog

from app.config import settings
from app.events import EventEnvelope

try:  # 감사 서비스(07) 포워딩 — fakeredis 는 프로세스 내부 전용이라 Pub/Sub이 닿지 않는다
    from shared.contracts.audit_sink import forward as forward_to_audit
except ImportError:  # pragma: no cover - shared/ 미탑재 환경 방어
    def forward_to_audit(envelope) -> None:  # type: ignore[misc]
        return None

logger = structlog.get_logger(__name__)

CHANNEL = "hr-events"
_HISTORY_MAX = 1000

Handler = Callable[[EventEnvelope], None]


def _build_client(url: str):
    """fakeredis:// 스킴이면 인메모리, 아니면 실제 Redis."""
    if url.startswith("fakeredis"):
        import fakeredis

        return fakeredis.FakeRedis(decode_responses=True)
    import redis

    return redis.Redis.from_url(url, decode_responses=True)


class EventBus:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or settings.redis_url
        self.client = _build_client(self.url)
        self._handlers: dict[str, list[Handler]] = {}
        self._history: deque[EventEnvelope] = deque(maxlen=_HISTORY_MAX)
        self._counts: dict[str, int] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # --- 발행 ---
    def publish(self, envelope: EventEnvelope) -> EventEnvelope:
        raw = envelope.model_dump_json()
        with self._lock:
            self._history.append(envelope)
            self._counts[envelope.event_type] = self._counts.get(envelope.event_type, 0) + 1
        self.client.publish(CHANNEL, raw)
        forward_to_audit(envelope)
        logger.info(
            "event_published",
            event_type=envelope.event_type,
            event_id=envelope.event_id,
            round_id=envelope.round_id,
        )
        # 인프로세스 구독자에게도 즉시 전달 (PoC: 리스너 스레드 없이도 동작)
        self._dispatch(envelope)
        return envelope

    # --- 구독 ---
    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)
        logger.info("event_subscribed", event_type=event_type)

    def _dispatch(self, envelope: EventEnvelope) -> None:
        for handler in self._handlers.get(envelope.event_type, []):
            try:
                handler(envelope)
            except Exception as exc:  # 구독자 예외가 발행을 막지 않도록
                logger.error(
                    "event_handler_failed",
                    event_type=envelope.event_type,
                    error=str(exc),
                )

    def start_listener(self) -> None:
        """외부(다른 서비스)에서 온 이벤트 수신용 백그라운드 스레드."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._listen, name="event-bus", daemon=True)
        self._thread.start()

    def _listen(self) -> None:  # pragma: no cover - 백그라운드 스레드
        pubsub = self.client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(CHANNEL)
        try:
            while not self._stop.is_set():
                message = pubsub.get_message(timeout=1.0)
                if not message:
                    continue
                try:
                    envelope = EventEnvelope(**json.loads(message["data"]))
                except Exception as exc:
                    logger.error("event_decode_failed", error=str(exc))
                    continue
                if envelope.producer == "response-collector":
                    continue  # 자기 발행 이벤트는 publish() 에서 이미 디스패치됨
                self._dispatch(envelope)
        finally:
            pubsub.close()

    def stop_listener(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # --- 조회 (테스트 · /metrics) ---
    def history(self, event_type: str | None = None) -> list[EventEnvelope]:
        with self._lock:
            items = list(self._history)
        if event_type is None:
            return items
        return [e for e in items if e.event_type == event_type]

    def counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def reset(self) -> None:
        with self._lock:
            self._history.clear()
            self._counts.clear()


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_event_bus() -> None:
    """테스트용 — 버스 인스턴스 초기화."""
    global _bus
    if _bus is not None:
        _bus.stop_listener()
    _bus = None
