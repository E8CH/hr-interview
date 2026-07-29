"""이벤트 수집 — event_log 저장 · 멱등성 · 18종 커버리지 · Redis 구독"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.events import ALL_EVENT_TYPES, EventType
from app.infrastructure.repository import EventRepository
from app.services.event_collector import ingest_event, ingest_many
from tests.conftest import make_event


def test_event_ingest_stores_row(session):
    """이벤트 수신 → event_log 저장 (명세 test_event_ingest)"""
    envelope = make_event(
        EventType.MASTER_REGISTERED,
        payload={"applicant_count": 88, "actor": "HR김민지"},
    )

    result = ingest_event(session, envelope)

    assert result.status == "stored"
    rows = EventRepository(session).by_round("R2026-Q3-01")
    assert len(rows) == 1
    row = rows[0]
    assert row.event_id == envelope["event_id"]
    assert row.event_type == EventType.MASTER_REGISTERED
    assert row.producer == "test"
    assert row.correlation_id == "corr-test"
    assert row.payload["applicant_count"] == 88
    assert row.received_at is not None


def test_ingest_is_idempotent_on_duplicate_event_id(session):
    """같은 event_id 재수신 시 중복 저장하지 않는다 (at-least-once 대응)"""
    envelope = make_event(EventType.RESPONSE_RECEIVED, payload={"org": "제1기술원"})

    first = ingest_event(session, envelope)
    second = ingest_event(session, envelope)

    assert first.status == "stored"
    assert second.status == "duplicate"
    assert len(EventRepository(session).by_round("R2026-Q3-01")) == 1


def test_ingest_rejects_envelope_without_event_type(session):
    result = ingest_event(session, {"round_id": "R2026-Q3-01"})

    assert result.status == "invalid"
    assert result.reason == "missing_event_type"
    assert EventRepository(session).total_count() == 0


def test_ingest_rejects_non_object(session):
    assert ingest_event(session, ["not", "a", "dict"]).status == "invalid"


def test_all_18_catalog_event_types_are_stored(session):
    """18종 이벤트 모두 수신·저장 확인 (완료 판정 체크리스트 1)"""
    envelopes = [
        make_event(event_type, minutes=index)
        for index, event_type in enumerate(ALL_EVENT_TYPES)
    ]

    results = ingest_many(session, envelopes)

    assert len(ALL_EVENT_TYPES) == 18
    assert all(r.status == "stored" for r in results)
    stored_types = set(EventRepository(session).count_by_type("R2026-Q3-01"))
    assert stored_types == set(ALL_EVENT_TYPES)


def test_event_log_has_no_mutation_api():
    """append-only 보장 — 리포지토리에 수정/삭제 메서드가 없어야 한다"""
    forbidden = {"update", "delete", "remove", "purge", "truncate"}
    exposed = {name for name in dir(EventRepository) if not name.startswith("_")}
    assert forbidden & exposed == set()


def test_events_without_round_id_are_stored_but_not_projected(session):
    from app.infrastructure.repository import KpiRepository

    envelope = make_event(EventType.MASTER_REGISTERED, payload={"applicant_count": 10})
    envelope["round_id"] = None

    assert ingest_event(session, envelope).status == "stored"
    assert EventRepository(session).total_count() == 1
    assert KpiRepository(session).latest(None, "총_대상자") is None


# --- Redis(FakeRedis) 구독 경로 --------------------------------------------


@pytest.mark.asyncio
async def test_collector_receives_event_from_wildcard_channel(monkeypatch):
    """wildcard 구독으로 실제 pub/sub 경로를 통과하는지 검증"""
    from app.config import get_settings
    from app.infrastructure.event_bus import get_event_bus
    from app.infrastructure.db import new_session
    from app.services.event_collector import EventCollector

    settings = get_settings()
    bus = get_event_bus()
    collector = EventCollector()
    await collector.start()

    envelope = make_event(
        EventType.SCHEDULE_LOCKED,
        payload={"schedule_id": "S1", "assignments_count": 78},
    )
    await bus.publish(
        settings.channel_for(EventType.SCHEDULE_LOCKED), json.dumps(envelope)
    )

    # 수신 대기 (성공 기준: 1초 이내)
    deadline = 1.0
    elapsed = 0.0
    stored = 0
    while elapsed < deadline:
        await asyncio.sleep(0.05)
        elapsed += 0.05
        db = new_session()
        try:
            stored = EventRepository(db).total_count()
        finally:
            db.close()
        if stored:
            break

    await collector.stop()
    assert stored == 1, "1초 이내에 이벤트가 event_log에 저장되어야 한다"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "channel",
    [
        "hr.MASTER_REGISTERED",   # 07 명세 스킴
        "hr-events",              # Service 01 (고정 채널)
        "DISTRIBUTION_APPROVED",  # Service 02 (event_type 그대로)
        "events",                 # Service 04 (설정값)
    ],
)
async def test_collector_receives_every_publisher_channel_scheme(channel):
    """발행 측 채널 스킴이 3종으로 갈려 있어도 수신한다.

    공통 계약이 채널명을 규정하지 않아 01/02/04가 서로 다른 이름을 쓴다.
    07은 채널이 아니라 봉투의 event_type으로 라우팅하므로 넓게 받는다.
    """
    from app.infrastructure.db import new_session
    from app.infrastructure.event_bus import get_event_bus
    from app.services.event_collector import EventCollector

    bus = get_event_bus()
    collector = EventCollector()
    await collector.start()

    await bus.publish(
        channel,
        json.dumps(make_event(EventType.MASTER_REGISTERED,
                              payload={"applicant_count": 88})),
    )

    stored = 0
    for _ in range(20):
        await asyncio.sleep(0.05)
        db = new_session()
        try:
            stored = EventRepository(db).total_count()
        finally:
            db.close()
        if stored:
            break

    await collector.stop()
    assert stored == 1, f"채널 '{channel}'로 발행된 이벤트를 수신하지 못했다"


@pytest.mark.asyncio
async def test_collector_ignores_malformed_payload():
    from app.config import get_settings
    from app.infrastructure.event_bus import get_event_bus
    from app.services.event_collector import STATS, EventCollector

    settings = get_settings()
    bus = get_event_bus()
    collector = EventCollector()
    await collector.start()

    await bus.publish(settings.channel_for("BROKEN"), "{not-json")
    await asyncio.sleep(0.3)
    await collector.stop()

    assert STATS.invalid >= 1
