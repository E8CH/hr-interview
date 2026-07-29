"""감사 API — 이벤트 타임라인 · 감사 질의 · (PoC) mock 이벤트 주입"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.responses import ApiError, ErrorCode, ok
from app.domain.event import AuditQueryRequest, IngestEvent
from app.events import ALL_EVENT_TYPES
from app.infrastructure.db import session_scope
from app.infrastructure.repository import EventRepository
from app.services.event_collector import STATS, ingest_event

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("/timeline")
def get_timeline(
    round_id: str = Query(..., description="회차 ID"),
    event_type: str | None = Query(None, description="쉼표 구분 다중 지정 가능"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(session_scope),
):
    """이벤트 타임라인 — 시간 오름차순"""
    event_types = (
        [t.strip() for t in event_type.split(",") if t.strip()] if event_type else None
    )
    rows = EventRepository(session).query(
        round_id=round_id, event_types=event_types, limit=limit, offset=offset
    )
    return ok([row.to_dict() for row in rows])


@router.post("/query")
def audit_query(
    request: AuditQueryRequest = Body(...),
    session: Session = Depends(session_scope),
):
    """감사 감정 질의 — 회차·행위자·이벤트타입·기간 복합 필터"""
    if request.from_ and request.to and request.from_ > request.to:
        raise ApiError(
            ErrorCode.VALIDATION_FAILED, "from은 to보다 이후일 수 없습니다", 400
        )

    rows = EventRepository(session).query(
        round_id=request.round_id,
        event_types=request.event_types,
        actor=request.actor,
        producer=request.producer,
        correlation_id=request.correlation_id,
        time_from=request.from_,
        time_to=request.to,
        limit=request.limit,
        offset=request.offset,
    )
    return ok(
        {
            "count": len(rows),
            "events": [row.to_dict() for row in rows],
        }
    )


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
def ingest(
    events: IngestEvent | list[IngestEvent] = Body(...),
    session: Session = Depends(session_scope),
):
    """PoC 전용 — 다른 서비스의 이벤트를 모사 주입한다.

    Service 07은 이벤트를 발행하지 않는다. 이 엔드포인트는 Redis 구독과
    **동일한 수집 함수**(`ingest_event`)를 호출하므로, 여기로 넣은 이벤트는
    실제 수집 경로를 그대로 통과한다.
    """
    batch = events if isinstance(events, list) else [events]
    results = []
    for item in batch:
        result = ingest_event(session, item.model_dump())
        STATS.record(result)
        results.append(
            {
                "event_id": result.event_id,
                "event_type": result.event_type,
                "status": result.status,
                "reason": result.reason,
            }
        )

    stored = sum(1 for r in results if r["status"] == "stored")
    return ok({"accepted": stored, "total": len(results), "results": results})


@router.get("/events/stats")
def event_stats(session: Session = Depends(session_scope)):
    """수집 현황 — 18종 커버리지 확인용"""
    repo = EventRepository(session)
    covered = {}
    for round_id in repo.distinct_rounds():
        for event_type, count in repo.count_by_type(round_id).items():
            covered[event_type] = covered.get(event_type, 0) + count

    return ok(
        {
            "total_events": repo.total_count(),
            "catalog_size": len(ALL_EVENT_TYPES),
            "covered_types": sorted(covered),
            "missing_types": sorted(set(ALL_EVENT_TYPES) - set(covered)),
            "counts_by_type": covered,
            "collector": {
                "received": STATS.received,
                "stored": STATS.stored,
                "duplicate": STATS.duplicate,
                "invalid": STATS.invalid,
            },
        }
    )
