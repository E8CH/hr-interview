"""Service 06 — Notification Hub

포트: 8006 · DB 스키마: notif_db · 명세: bmad/06_notification_hub.md

로컬 실행 (Docker 불필요):
    uvicorn app.main:app --port 8006 --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import ROUTERS
from app.config import settings
from app.domain.models import DeadLetter, Notification, Template
from app.events import PUBLISHED_EVENTS, SUBSCRIBED_EVENTS, register_subscribers
from app.infrastructure.db import ensure_db, get_session_factory, session_scope
from app.infrastructure.event_bus import get_event_bus
from app.logging_config import configure_logging
from app.responses import ApiError, fail, ok
from app.services.retry_worker import RetryWorker
from app.services.seed import seed_all

log = structlog.get_logger(__name__)

_worker: RetryWorker | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _worker
    configure_logging()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)

    ensure_db()
    with session_scope() as session:
        seeded = seed_all(session)
    log.info("seed_completed", **seeded)

    bus = get_event_bus()
    register_subscribers(bus)
    if settings.event_listener_enabled:
        bus.start_listener()

    if settings.worker_enabled:
        _worker = RetryWorker()
        _worker.start()

    log.info(
        "service_started",
        service=settings.SERVICE_NAME,
        port=settings.service_port,
        use_mock=settings.use_mock,
    )
    try:
        yield
    finally:
        if _worker is not None:
            await _worker.stop()
            _worker = None
        bus.stop_listener()
        log.info("service_stopped", service=settings.SERVICE_NAME)


app = FastAPI(
    title="Notification Hub",
    version="1.0.0",
    description="통합 알림 발송 허브 (이메일 · Slack · SMS)",
    lifespan=lifespan,
)

for _router in ROUTERS:
    app.include_router(_router)


# --- 공통 에러 응답 규약 ---
@app.exception_handler(ApiError)
async def _api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code, content=fail(exc.code, exc.message))


@app.exception_handler(StarletteHTTPException)
async def _http_error_handler(request: Request, exc: StarletteHTTPException):
    code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
    return JSONResponse(status_code=exc.status_code, content=fail(code, str(exc.detail)))


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    messages = "; ".join(
        f"{'.'.join(str(p) for p in err.get('loc', []))}: {err.get('msg', '')}"
        for err in exc.errors()
    )
    return JSONResponse(
        status_code=422, content=fail("VALIDATION_FAILED", messages or "요청 형식 오류")
    )


# --- 헬스체크 · 메트릭 ---
@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "06-notification-hub"}


@app.get("/")
def root():
    return ok(
        {
            "service": "06-notification-hub",
            "version": app.version,
            "publishes": list(PUBLISHED_EVENTS),
            "subscribes": list(SUBSCRIBED_EVENTS),
            "docs": "/docs",
        }
    )


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    """Prometheus 텍스트 포맷."""
    session = get_session_factory()()
    try:
        status_rows = session.execute(
            select(Notification.status, func.count()).group_by(Notification.status)
        ).all()
        dead_count = session.execute(
            select(func.count(DeadLetter.dead_letter_id))
        ).scalar_one()
        template_count = session.execute(
            select(func.count(Template.template_id))
        ).scalar_one()
        attempts = session.execute(
            select(func.coalesce(func.sum(Notification.attempt_count), 0))
        ).scalar_one()
    finally:
        session.close()

    bus = get_event_bus()
    event_counts: dict[str, int] = {}
    for event in bus.published:
        event_counts[event["event_type"]] = event_counts.get(event["event_type"], 0) + 1

    lines = [
        "# HELP notification_total 상태별 알림 건수",
        "# TYPE notification_total gauge",
    ]
    for status, count in sorted(status_rows):
        lines.append(f'notification_total{{status="{status}"}} {count}')
    lines += [
        "# HELP notification_dead_letter_total dead letter 큐 적재 건수",
        "# TYPE notification_dead_letter_total gauge",
        f"notification_dead_letter_total {dead_count}",
        "# HELP notification_attempts_total 누적 발송 시도 횟수",
        "# TYPE notification_attempts_total counter",
        f"notification_attempts_total {attempts}",
        "# HELP notification_templates_total 등록된 템플릿 수",
        "# TYPE notification_templates_total gauge",
        f"notification_templates_total {template_count}",
        "# HELP notification_events_published_total 발행 이벤트 수",
        "# TYPE notification_events_published_total counter",
    ]
    for event_type, count in sorted(event_counts.items()):
        lines.append(
            f'notification_events_published_total{{event_type="{event_type}"}} {count}'
        )
    return "\n".join(lines) + "\n"
