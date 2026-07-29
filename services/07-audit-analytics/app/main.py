"""Service 07: Audit & Analytics
포트: 8007 · DB 스키마: audit_db
명세: bmad/07_audit_analytics.md

역할: 18종 이벤트 append-only 수집 · 실시간 KPI 대시보드 · 위험 신호 감지 ·
      회차 종합 리포트 · Before/After 비교
이벤트 발행: 없음 (수집 전용)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api import audit, dashboard, reports
from app.api.responses import ApiError, ErrorCode, fail
from app.config import get_settings
from app.infrastructure.db import init_db
from app.services.event_collector import STATS, get_collector


def configure_logging(level: str) -> None:
    logging.basicConfig(format="%(message)s", level=getattr(logging, level, logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level, logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)

    init_db()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)

    collector = None
    if settings.enable_collector:
        collector = get_collector()
        await collector.start()

    log.info(
        "service.started",
        service=settings.service_name,
        port=settings.service_port,
        database=settings.database_url,
        event_bus=settings.redis_url,
        use_mock=settings.use_mock,
    )
    try:
        yield
    finally:
        if collector is not None:
            await collector.stop()
        log.info("service.stopped", service=settings.service_name)


app = FastAPI(
    title="Audit & Analytics",
    version="1.0.0",
    description="HR 면접일정 시스템 — 감사 로그 수집 및 실시간 분석 (Service 07)",
    lifespan=lifespan,
)

app.include_router(dashboard.router)
app.include_router(reports.router)
app.include_router(audit.router)


# --- 공통 에러 봉투 ---------------------------------------------------------


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code, content=fail(exc.code, exc.message))


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=fail(ErrorCode.VALIDATION_FAILED, str(exc.errors())),
    )


# --- 운영 엔드포인트 --------------------------------------------------------


@app.get("/healthz", tags=["ops"])
def healthz():
    return {"status": "ok", "service": "07-audit-analytics"}


@app.get("/metrics", response_class=PlainTextResponse, tags=["ops"])
def metrics():
    """Prometheus 텍스트 포맷"""
    lines = [
        "# HELP audit_events_received_total 수신한 이벤트 총계",
        "# TYPE audit_events_received_total counter",
        f"audit_events_received_total {STATS.received}",
        "# HELP audit_events_stored_total event_log에 저장된 이벤트 총계",
        "# TYPE audit_events_stored_total counter",
        f"audit_events_stored_total {STATS.stored}",
        "# HELP audit_events_duplicate_total 중복으로 무시된 이벤트 총계",
        "# TYPE audit_events_duplicate_total counter",
        f"audit_events_duplicate_total {STATS.duplicate}",
        "# HELP audit_events_invalid_total 파싱·검증 실패 이벤트 총계",
        "# TYPE audit_events_invalid_total counter",
        f"audit_events_invalid_total {STATS.invalid}",
        "# HELP audit_events_by_type_total 이벤트 타입별 저장 건수",
        "# TYPE audit_events_by_type_total counter",
    ]
    for event_type, count in sorted(STATS.by_type.items()):
        lines.append(f'audit_events_by_type_total{{event_type="{event_type}"}} {count}')
    return "\n".join(lines) + "\n"


@app.get("/", tags=["ops"])
def root():
    return {
        "data": {
            "service": "07-audit-analytics",
            "version": "1.0.0",
            "endpoints": [
                "/api/v1/dashboard/kpi",
                "/api/v1/dashboard/organizations",
                "/api/v1/dashboard/risks",
                "/api/v1/audit/timeline",
                "/api/v1/audit/query",
                "/api/v1/audit/events",
                "/api/v1/reports/rounds/{round_id}",
                "/api/v1/reports/before-after",
            ],
        },
        "error": None,
    }
