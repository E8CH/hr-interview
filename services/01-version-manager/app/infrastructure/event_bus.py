"""이벤트 버스 — Redis Pub/Sub (PoC: fakeredis 인메모리).

REDIS_URL=fakeredis:// 이면 fakeredis, redis:// 이면 실제 Redis 사용.
발행 시 structlog로 로그를 남겨 '실제 발행'을 확인 가능하게 한다.
"""
import json

from app.config import get_settings
from app.logging_conf import get_logger

try:  # 감사 서비스(07) 포워딩 — fakeredis 는 프로세스 내부 전용이라 Pub/Sub이 닿지 않는다
    from shared.contracts.audit_sink import forward as forward_to_audit
except ImportError:  # pragma: no cover - shared/ 미탑재 환경 방어
    def forward_to_audit(envelope) -> None:  # type: ignore[misc]
        return None

log = get_logger("event-bus")

_CHANNEL = "hr-events"


class EventBus:
    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._client = self._connect(redis_url)
        self.published: list[dict] = []  # 테스트/디버깅용 in-memory 기록

    @staticmethod
    def _connect(url: str):
        if url.startswith("fakeredis"):
            import fakeredis

            return fakeredis.FakeStrictRedis(decode_responses=True)
        import redis

        return redis.Redis.from_url(url, decode_responses=True)

    def publish(self, envelope: dict) -> int:
        """이벤트 봉투(dict)를 채널에 발행. 구독자 수 반환."""
        body = json.dumps(envelope, ensure_ascii=False, default=str)
        receivers = self._client.publish(_CHANNEL, body)
        self.published.append(envelope)
        forward_to_audit(envelope)
        log.info(
            "event_published",
            event_type=envelope.get("event_type"),
            round_id=envelope.get("round_id"),
            event_id=envelope.get("event_id"),
            producer=envelope.get("producer"),
            receivers=receivers,
        )
        return receivers


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus(get_settings().redis_url)
    return _bus
