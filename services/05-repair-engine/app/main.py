"""Service 05 — Repair Engine
포트: 8005
명세: bmad/05_repair_engine.md

로컬 실행: uvicorn app.main:app --port 8005 --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import audit, locks, plans, repair
from app.api.schemas import fail
from app.config import settings
from app.events import register_subscribers
from app.infrastructure.db import init_db
from app.infrastructure.event_bus import event_bus
from app.services.lock_service import LockError
from app.services.repair_service import RepairError

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO),
                    format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    register_subscribers()
    logging.getLogger("repair-engine").info(
        "repair-engine 기동 · mock=%s db=%s", settings.use_mock, settings.database_url)
    yield


app = FastAPI(title="Repair Engine", version="1.0.0",
              description="노쇼·취소 대응 안전 재편성 엔진 (Service 05)",
              lifespan=lifespan)

app.include_router(repair.router)
app.include_router(plans.router)
app.include_router(locks.router)
app.include_router(audit.router)


# --- 공통 에러 응답 ---
@app.exception_handler(RepairError)
def _repair_error(_: Request, exc: RepairError):
    return JSONResponse(status_code=exc.status_code, content=fail(exc.code, exc.message))


@app.exception_handler(LockError)
def _lock_error(_: Request, exc: LockError):
    return JSONResponse(status_code=422, content=fail(exc.code, exc.message))


@app.exception_handler(RequestValidationError)
def _validation_error(_: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422,
                        content=fail("VALIDATION_FAILED", str(exc.errors())))


@app.exception_handler(StarletteHTTPException)
def _http_error(_: Request, exc: StarletteHTTPException):
    code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
    return JSONResponse(status_code=exc.status_code, content=fail(code, str(exc.detail)))


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "05-repair-engine"}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    published = event_bus.published()
    counts: dict[str, int] = {}
    for e in published:
        counts[e.get("event_type", "UNKNOWN")] = counts.get(e.get("event_type", "UNKNOWN"), 0) + 1
    lines = [
        "# HELP repair_events_published_total 발행된 이벤트 수",
        "# TYPE repair_events_published_total counter",
    ]
    for event_type, count in sorted(counts.items()):
        lines.append(f'repair_events_published_total{{event_type="{event_type}"}} {count}')
    lines += [
        "# HELP repair_service_up 서비스 기동 여부",
        "# TYPE repair_service_up gauge",
        "repair_service_up 1",
    ]
    return "\n".join(lines) + "\n"


@app.get("/")
def root():
    return {"data": {"service": "05-repair-engine", "version": "1.0.0",
                     "docs": "/docs"}, "error": None}
