"""이벤트 버스 — Redis Pub/Sub (PoC: fakeredis 인메모리)

설계:
- publish()는 (1) 인프로세스 구독자에게 즉시 디스패치하고 (2) Redis 채널로 발행한다.
  인프로세스 디스패치 덕분에 테스트가 스레드 타이밍에 의존하지 않는다.
- 리스너 스레드는 다른 프로세스(타 서비스)가 올린 이벤트만 처리한다.
  자기 자신이 발행한 이벤트(producer == scheduler)는 중복 디스패치를 막기 위해 건너뛴다.
"""
from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from app.config import settings
from app.infrastructure.contracts import EventEnvelope  # (import 부수효과: sys.path 부트스트랩)

try:  # 감사 서비스(07) 포워딩 — fakeredis 는 프로세스 내부 전용이라 Pub/Sub이 닿지 않는다
    from shared.contracts.audit_sink import forward as forward_to_audit
except ImportError:  # pragma: no cover - shared/ 미탑재 환경 방어
    def forward_to_audit(envelope) -> None:  # type: ignore[misc]
        return None

logger = logging.getLogger(__name__)

Handler = Callable[[EventEnvelope], None]


def _make_client(url: str):
    if url.startswith("fakeredis://"):
        import fakeredis

        return fakeredis.FakeStrictRedis(decode_responses=True)
    import redis

    return redis.Redis.from_url(url, decode_responses=True)


class EventBus:
    def __init__(self, url: str | None = None, channel: str | None = None) -> None:
        self.url = url or settings.redis_url
        self.channel = channel or settings.event_channel
        self._client = _make_client(self.url)
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._published: list[EventEnvelope] = []
        # 이미 디스패치한 event_id (리스너 스레드의 중복 전달 차단, 최근 2048건만 유지)
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque(maxlen=2048)
        self._thread: threading.Thread | None = None
        self._pubsub: Any = None
        self._stop = threading.Event()

    # -- 발행 -------------------------------------------------------------
    def publish(
        self,
        event_type: str,
        round_id: str,
        payload: dict,
        correlation_id: str,
        producer: str | None = None,
    ) -> EventEnvelope:
        envelope = EventEnvelope(
            event_type=event_type,
            round_id=round_id,
            producer=producer or settings.producer,
            correlation_id=correlation_id,
            payload=payload,
        )
        self._published.append(envelope)
        body = envelope.model_dump_json()
        try:
            self._client.publish(self.channel, body)
        except Exception as exc:  # pragma: no cover - 인프라 장애 경로
            logger.warning("event publish failed: %s", exc)
        forward_to_audit(envelope)
        self._dispatch(envelope)
        return envelope

    # -- 구독 -------------------------------------------------------------
    def subscribe(self, event_type: str, handler: Handler) -> None:
        """같은 핸들러의 중복 등록은 무시 (구독 등록이 여러 번 호출돼도 안전)"""
        if handler in self._handlers[event_type]:
            return
        self._handlers[event_type].append(handler)

    def _mark_seen(self, event_id: str) -> None:
        if len(self._seen_order) == self._seen_order.maxlen and self._seen_order:
            self._seen.discard(self._seen_order[0])
        self._seen_order.append(event_id)
        self._seen.add(event_id)

    def _dispatch(self, envelope: EventEnvelope) -> None:
        self._mark_seen(envelope.event_id)
        for handler in self._handlers.get(envelope.event_type, []):
            try:
                handler(envelope)
            except Exception as exc:  # pragma: no cover - 핸들러 예외 격리
                logger.warning("handler error for %s: %s", envelope.event_type, exc)

    def handle_raw(self, raw: str) -> EventEnvelope | None:
        """외부에서 들어온 원시 메시지를 봉투로 파싱 후 디스패치"""
        try:
            data = json.loads(raw)
            envelope = EventEnvelope(**data)
        except Exception as exc:
            logger.warning("malformed event dropped: %s", exc)
            return None
        if envelope.producer == settings.producer:
            return None  # 자기 발행분은 이미 인프로세스로 처리됨
        if envelope.event_id in self._seen:
            return None  # 같은 프로세스에서 이미 디스패치됨 (리스너 스레드 중복 전달)
        self._dispatch(envelope)
        return envelope

    # -- 리스너 스레드 -----------------------------------------------------
    def start_listener(self) -> None:
        if self._thread is not None:
            return
        try:
            self._pubsub = self._client.pubsub(ignore_subscribe_messages=True)
            self._pubsub.subscribe(self.channel)
        except Exception as exc:  # pragma: no cover
            logger.warning("listener subscribe failed: %s", exc)
            return

        def _run() -> None:
            while not self._stop.is_set():
                try:
                    msg = self._pubsub.get_message(timeout=0.5)
                except Exception:  # pragma: no cover
                    break
                if msg and msg.get("type") == "message":
                    self.handle_raw(msg["data"])

        self._stop.clear()
        self._thread = threading.Thread(target=_run, name="event-listener", daemon=True)
        self._thread.start()

    def stop_listener(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self._pubsub is not None:
            try:
                self._pubsub.close()
            except Exception:  # pragma: no cover
                pass
            self._pubsub = None

    # -- 테스트/메트릭 보조 -------------------------------------------------
    @property
    def published(self) -> list[EventEnvelope]:
        return list(self._published)

    def published_of(self, event_type: str) -> list[EventEnvelope]:
        return [e for e in self._published if e.event_type == event_type]

    def reset(self) -> None:
        self._published.clear()


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_event_bus() -> None:  # 테스트 격리용
    global _bus
    if _bus is not None:
        _bus.stop_listener()
    _bus = None
