"""Service: Scheduler
포트: 8004
명세: bmad/04_scheduler.md
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

from app import events
from app.api import interviewers as interviewers_api
from app.api import rules as rules_api
from app.api import schedules as schedules_api
from app.config import settings
from app.errors import ApiError, ok
from app.infrastructure.db import SessionLocal, init_db
from app.infrastructure.event_bus import get_event_bus
from app.services import schedule_service

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger(service=settings.service_name)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    settings.ensure_storage()
    events.register_subscriptions()
    bus = get_event_bus()
    bus.start_listener()

    seeded = 0
    if settings.use_mock:
        with SessionLocal() as db:
            seeded = schedule_service.seed_interviewers(db)
    log.info("scheduler.startup", port=settings.service_port, mock=settings.use_mock, seeded=seeded)
    try:
        yield
    finally:
        bus.stop_listener()
        log.info("scheduler.shutdown")


app = FastAPI(title="Scheduler", version="1.0.0", lifespan=lifespan)

app.include_router(schedules_api.router)
app.include_router(rules_api.router)
app.include_router(interviewers_api.router)


@app.exception_handler(ApiError)
async def _api_error_handler(_: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code, content=exc.body())


@app.exception_handler(RequestValidationError)
async def _validation_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={
            "data": None,
            "error": {
                "code": "VALIDATION_FAILED",
                "message": "요청 본문이 올바르지 않습니다",
                "detail": {"errors": str(exc.errors())},
            },
        },
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "04-scheduler"}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    bus = get_event_bus()
    lines = [
        "# HELP scheduler_schedules_generated_total 생성된 시간표 수",
        "# TYPE scheduler_schedules_generated_total counter",
        f"scheduler_schedules_generated_total {schedule_service.METRICS['schedules_generated_total']}",
        "# HELP scheduler_assignments_total 누적 배정 건수",
        "# TYPE scheduler_assignments_total counter",
        f"scheduler_assignments_total {schedule_service.METRICS['assignments_total']}",
        "# HELP scheduler_hard_violations_total 누적 하드 제약 위반 건수",
        "# TYPE scheduler_hard_violations_total counter",
        f"scheduler_hard_violations_total {schedule_service.METRICS['hard_violations_total']}",
        "# HELP scheduler_lock_upgrades_total 락 단계 상승 횟수",
        "# TYPE scheduler_lock_upgrades_total counter",
        f"scheduler_lock_upgrades_total {schedule_service.METRICS['lock_upgrades_total']}",
        "# HELP scheduler_events_published_total 발행 이벤트 수",
        "# TYPE scheduler_events_published_total counter",
        f"scheduler_events_published_total {len(bus.published)}",
    ]
    return "\n".join(lines) + "\n"


@app.get("/")
def root():
    return ok({"service": "04-scheduler", "version": "1.0.0", "algorithms": ["v1", "v4", "v5"]})


@app.get("/api/v1/rounds/{round_id}/readiness")
def round_readiness(round_id: str):
    """구독 이벤트로 파악한 회차 준비 상태 (RESPONSE_RECEIVED / DISTRIBUTION_APPROVED)"""
    return ok(events.readiness(round_id))
