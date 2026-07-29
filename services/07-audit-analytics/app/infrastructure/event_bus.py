"""이벤트 버스 어댑터 — FakeRedis(PoC) / Redis(운영)

REDIS_URL 스킴으로 구현체를 고른다.
    fakeredis://   → fakeredis.aioredis (인메모리, 프로세스 내 공유)
    redis://...    → redis.asyncio

Service 07은 **수집 전용**이라 도메인 이벤트를 발행하지 않는다.
publish()는 테스트·시드에서 다른 서비스의 이벤트를 모사 주입할 때만 쓴다.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

# fakeredis는 FakeServer 인스턴스를 공유해야 publisher/subscriber가 같은
# 인메모리 버스를 본다. 모듈 레벨 싱글턴으로 유지.
_fake_server = None
_bus: "EventBus | None" = None


def _get_fake_server():
    global _fake_server
    if _fake_server is None:
        import fakeredis

        _fake_server = fakeredis.FakeServer()
    return _fake_server


class EventBus:
    """Redis Pub/Sub 최소 래퍼"""

    def __init__(self, url: str) -> None:
        self.url = url
        self._client = None

    @property
    def is_fake(self) -> bool:
        return self.url.startswith("fakeredis")

    def client(self):
        if self._client is None:
            if self.is_fake:
                import fakeredis.aioredis

                self._client = fakeredis.aioredis.FakeRedis(
                    server=_get_fake_server(), decode_responses=True
                )
            else:
                import redis.asyncio as aioredis

                self._client = aioredis.from_url(self.url, decode_responses=True)
        return self._client

    async def publish(self, channel: str, message: str) -> int:
        return await self.client().publish(channel, message)

    async def psubscribe(self, pattern: str) -> AsyncIterator[tuple[str, str]]:
        """패턴 구독 — (channel, data) 스트림을 yield"""
        pubsub = self.client().pubsub()
        await pubsub.psubscribe(pattern)
        log.info("event_bus.psubscribed", pattern=pattern, backend="fake" if self.is_fake else "redis")
        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message is None:
                    # 취소 지점 확보 (shutdown 시 CancelledError 수신)
                    await asyncio.sleep(0)
                    continue
                if message.get("type") not in ("pmessage", "message"):
                    continue
                channel = message.get("channel")
                data = message.get("data")
                if isinstance(channel, bytes):
                    channel = channel.decode()
                if isinstance(data, bytes):
                    data = data.decode()
                yield channel, data
        finally:
            try:
                await pubsub.punsubscribe(pattern)
                await pubsub.close()
            except Exception:  # pragma: no cover - 종료 경로 방어
                pass

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except AttributeError:  # pragma: no cover - 구버전 redis 호환
                await self._client.close()
            except Exception:  # pragma: no cover
                pass
            self._client = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus(get_settings().redis_url)
    return _bus


def reset_event_bus() -> None:
    """테스트 격리용 — 버스 싱글턴 초기화"""
    global _bus, _fake_server
    _bus = None
    _fake_server = None
