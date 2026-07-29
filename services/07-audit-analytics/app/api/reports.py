"""리포트 API — 회차 종합 리포트 · Before/After 비교"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.responses import ApiError, ErrorCode, ok
from app.infrastructure.db import session_scope
from app.infrastructure.repository import EventRepository, KpiRepository
from app.services.report_generator import ReportGenerator

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


@router.get("/rounds/{round_id}")
def get_round_report(
    round_id: str,
    response: Response,
    refresh: bool = Query(False, description="캐시 무시하고 재생성"),
    session: Session = Depends(session_scope),
):
    """회차 종합 리포트.

    캐시 적중 여부는 `X-Cache` 헤더로 노출한다 (응답 본문은 명세 스키마 유지).
    """
    events = EventRepository(session)
    if not events.by_round(round_id):
        raise ApiError(
            ErrorCode.NOT_FOUND, f"회차 '{round_id}'의 이벤트가 없습니다", 404
        )

    report, cache_hit = ReportGenerator(session).round_summary(
        round_id, use_cache=not refresh
    )
    response.headers["X-Cache"] = "HIT" if cache_hit else "MISS"
    return ok(report)


@router.get("/before-after")
def get_before_after(
    rounds: str = Query(..., description="비교할 두 회차 — 'before,after'"),
    session: Session = Depends(session_scope),
):
    """Before/After 개선 지표 비교"""
    parts = [r.strip() for r in rounds.split(",") if r.strip()]
    if len(parts) != 2:
        raise ApiError(
            ErrorCode.VALIDATION_FAILED,
            "rounds는 'before,after' 형식의 회차 2개여야 합니다",
            400,
        )

    before_round, after_round = parts
    kpi = KpiRepository(session)
    events = EventRepository(session)
    known = set(events.distinct_rounds())

    def has_data(round_id: str) -> bool:
        if round_id in known:
            return True
        # baseline 회차는 이벤트 없이 kpi_snapshots만 주입될 수 있다
        from app.domain.kpi import BEFORE_AFTER_METRICS

        return any(
            kpi.latest(round_id, metric) is not None for metric, _ in BEFORE_AFTER_METRICS
        )

    missing = [r for r in (before_round, after_round) if not has_data(r)]
    if missing:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            f"데이터가 없는 회차: {', '.join(missing)}",
            404,
        )

    return ok(ReportGenerator(session).before_after(before_round, after_round))
