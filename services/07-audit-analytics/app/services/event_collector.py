"""이벤트 수집기 — `hr.*` wildcard 구독 → event_log 저장 → 프로젝션

Service 07은 수집 전용이다. 이 모듈은 이벤트를 발행하지 않는다.

수집 경로는 단 하나(`ingest_event`)로 통일한다.
    - Redis Pub/Sub 구독 (운영/PoC 공통)
    - POST /api/v1/audit/events (PoC mock 주입)
둘 다 같은 함수를 타므로 테스트가 실제 경로를 검증한다.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime

import structlog
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.report import ReportType
from app.infrastructure.db import new_session, utcnow
from app.infrastructure.event_bus import EventBus, get_event_bus
from app.infrastructure.repository import EventRepository, ReportRepository
from app.services.projector import Projector

log = structlog.get_logger(__name__)


@dataclass
class IngestResult:
    status: str  # "stored" | "duplicate" | "invalid"
    event_id: str | None = None
    event_type: str | None = None
    reason: str | None = None


@dataclass
class CollectorStats:
    """Prometheus /metrics 노출용 카운터"""

    received: int = 0
    stored: int = 0
    duplicate: int = 0
    invalid: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    last_event_at: datetime | None = None

    def record(self, result: IngestResult) -> None:
        self.received += 1
        if result.status == "stored":
            self.stored += 1
            self.last_event_at = utcnow()
            if result.event_type:
                self.by_type[result.event_type] = (
                    self.by_type.get(result.event_type, 0) + 1
                )
        elif result.status == "duplicate":
            self.duplicate += 1
        else:
            self.invalid += 1


STATS = CollectorStats()


def _coerce_timestamp(raw) -> datetime:
    from app.domain.event import parse_dt

    parsed = parse_dt(raw)
    return parsed if parsed is not None else utcnow()


def ingest_event(session: Session, envelope: dict) -> IngestResult:
    """이벤트 1건 수집. 저장 → 프로젝션 → 캐시 무효화까지 한 트랜잭션.

    envelope는 00_SHARED_CONTRACT.md의 봉투 형식. event_type만 필수로 본다
    (다른 서비스의 payload 스키마 편차는 프로젝터가 방어적으로 흡수).
    """
    if not isinstance(envelope, dict):
        return IngestResult(status="invalid", reason="not_an_object")

    event_type = envelope.get("event_type")
    if not event_type:
        return IngestResult(status="invalid", reason="missing_event_type")

    from uuid import uuid4

    event_id = str(envelope.get("event_id") or uuid4())
    round_id = envelope.get("round_id")

    events = EventRepository(session)
    row = events.append(
        event_id=event_id,
        event_type=str(event_type),
        timestamp=_coerce_timestamp(envelope.get("timestamp")),
        round_id=round_id,
        producer=envelope.get("producer"),
        correlation_id=envelope.get("correlation_id"),
        payload=envelope.get("payload") or {},
    )
    if row is None:
        return IngestResult(
            status="duplicate", event_id=event_id, event_type=str(event_type)
        )

    Projector(session).project(row)

    # 캐시 무효화: 동일 회차에 새 이벤트가 도착하면 리포트 캐시는 낡은 것이 된다
    if round_id:
        ReportRepository(session).invalidate(round_id, ReportType.ROUND_SUMMARY)

    session.commit()
    return IngestResult(status="stored", event_id=event_id, event_type=str(event_type))


def ingest_many(session: Session, envelopes: list[dict]) -> list[IngestResult]:
    results = []
    for envelope in envelopes:
        result = ingest_event(session, envelope)
        STATS.record(result)
        results.append(result)
    return results


class EventCollector:
    """백그라운드 구독 루프"""

    def __init__(self, bus: EventBus | None = None, pattern: str | None = None) -> None:
        settings = get_settings()
        self.bus = bus or get_event_bus()
        self.pattern = pattern or settings.event_channel_pattern
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()

    async def _handle_message(self, channel: str, data: str) -> None:
        try:
            envelope = json.loads(data)
        except (TypeError, ValueError):
            STATS.record(IngestResult(status="invalid", reason="bad_json"))
            log.warning("collector.bad_json", channel=channel)
            return

        # 채널명이 아니라 봉투의 event_type으로 라우팅한다 (채널 규약 미확정 대응)
        def _work() -> IngestResult:
            session = new_session()
            try:
                return ingest_event(session, envelope)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        try:
            result = await asyncio.to_thread(_work)
        except Exception as exc:  # pragma: no cover - 저장 실패 방어
            STATS.record(IngestResult(status="invalid", reason=str(exc)))
            log.error("collector.ingest_failed", channel=channel, error=str(exc))
            return

        STATS.record(result)
        log.debug(
            "collector.ingested",
            channel=channel,
            event_type=result.event_type,
            status=result.status,
        )

    async def _run(self) -> None:
        try:
            stream = self.bus.psubscribe(self.pattern)
            agen = stream.__aiter__()
            self._ready.set()
            async for channel, data in agen:
                await self._handle_message(channel, data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - 구독 실패 방어
            log.error("collector.subscribe_failed", error=str(exc))
            self._ready.set()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="audit-event-collector")
        # 구독이 실제로 걸릴 때까지 대기 (초기 이벤트 유실 방지)
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=5.0)
        except asyncio.TimeoutError:  # pragma: no cover
            log.warning("collector.start_timeout")
        log.info("collector.started", pattern=self.pattern)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # pragma: no cover
            pass
        self._task = None
        await self.bus.close()
        log.info("collector.stopped")


_collector: EventCollector | None = None


def get_collector() -> EventCollector:
    global _collector
    if _collector is None:
        _collector = EventCollector()
    return _collector


def reset_collector() -> None:
    global _collector
    _collector = None
