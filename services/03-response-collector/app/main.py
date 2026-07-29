"""Service 03 — Response Collector

포트: 8003 · DB 스키마: resp_db · 명세: bmad/03_response_collector.md

로컬 실행:
    uvicorn app.main:app --port 8003 --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api import form, patterns, reminders, requests, responses, rounds
from app.api.errors import register_error_handlers
from app.config import settings
from app.domain.invitee import Invitee
from app.domain.reminder import Reminder
from app.domain.request import Request
from app.domain.response import Response as ResponseModel
from app.events import PUBLISHED_EVENTS, SUBSCRIBED_EVENTS
from app.infrastructure.db import get_db, init_db
from app.infrastructure.event_bus import get_event_bus
from app.infrastructure.scheduler import start_scheduler, stop_scheduler
from app.logging_config import configure_logging
from app.schemas import ok
from app.subscribers import register_subscribers

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    register_subscribers()
    start_scheduler()
    logger.info(
        "service_started",
        service="03-response-collector",
        port=settings.service_port,
        database_url=settings.database_url,
        redis_url=settings.redis_url,
        use_mock=settings.use_mock,
    )
    yield
    stop_scheduler()
    get_event_bus().stop_listener()
    logger.info("service_stopped", service="03-response-collector")


app = FastAPI(
    title="Response Collector",
    description="면접위원 초대 발송 · 구조화 웹폼 응답 수집 · 3단계 리마인더 · 조직 응답 패턴 학습",
    version="1.0.0",
    lifespan=lifespan,
)

register_error_handlers(app)
app.include_router(requests.router)
app.include_router(responses.router)
app.include_router(rounds.router)
app.include_router(patterns.router)
app.include_router(reminders.router)
app.include_router(form.router)


@app.get("/healthz", tags=["ops"])
def healthz():
    return {"status": "ok", "service": "03-response-collector"}


@app.get("/", tags=["ops"])
def root():
    return ok(
        {
            "service": "03-response-collector",
            "version": app.version,
            "port": settings.service_port,
            "publishes": list(PUBLISHED_EVENTS),
            "subscribes": list(SUBSCRIBED_EVENTS),
            "docs": "/docs",
        }
    )


@app.get("/metrics", response_class=PlainTextResponse, tags=["ops"])
def metrics(db: Session = Depends(get_db)):
    """Prometheus 텍스트 포맷."""
    totals = {
        "requests": db.query(func.count(Request.request_id)).scalar() or 0,
        "invitees": db.query(func.count(Invitee.invitee_id)).scalar() or 0,
        "responses": db.query(func.count(ResponseModel.response_id)).scalar() or 0,
        "reminders": db.query(func.count(Reminder.reminder_id)).scalar() or 0,
    }
    escalated = (
        db.query(func.count(Reminder.reminder_id))
        .filter(Reminder.cc_supervisor.is_(True))
        .scalar()
        or 0
    )

    lines = [
        "# HELP respcol_requests_total 발송된 초대 요청 수",
        "# TYPE respcol_requests_total counter",
        f"respcol_requests_total {totals['requests']}",
        "# HELP respcol_invitees_total 초대된 면접위원 수",
        "# TYPE respcol_invitees_total counter",
        f"respcol_invitees_total {totals['invitees']}",
        "# HELP respcol_responses_total 수집된 응답 수",
        "# TYPE respcol_responses_total counter",
        f"respcol_responses_total {totals['responses']}",
        "# HELP respcol_reminders_total 발송된 리마인더 수",
        "# TYPE respcol_reminders_total counter",
        f"respcol_reminders_total {totals['reminders']}",
        "# HELP respcol_escalations_total Level 3 상급자 CC 에스컬레이션 수",
        "# TYPE respcol_escalations_total counter",
        f"respcol_escalations_total {escalated}",
        "# HELP respcol_events_published_total 발행된 이벤트 수",
        "# TYPE respcol_events_published_total counter",
    ]
    counts = get_event_bus().counts()
    for event_type in PUBLISHED_EVENTS:
        lines.append(
            f'respcol_events_published_total{{event_type="{event_type}"}} '
            f"{counts.get(event_type, 0)}"
        )

    pending = totals["invitees"] - totals["responses"]
    lines += [
        "# HELP respcol_pending_responses 미회신 건수",
        "# TYPE respcol_pending_responses gauge",
        f"respcol_pending_responses {max(pending, 0)}",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.service_port, reload=True)
