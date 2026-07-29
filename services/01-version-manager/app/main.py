"""Service 01: Version Manager (FastAPI 앱).

포트: 8001 · 명세: bmad/01_version_manager.md · 공통계약: bmad/00_SHARED_CONTRACT.md
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.versions import router as versions_router
from app.infrastructure.db import init_db
from app.logging_conf import get_logger
from app.schemas import err
from app.services.version_service import VersionError

log = get_logger("main")

# 간단한 in-process 메트릭 카운터
_METRICS = {"requests_total": 0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info("service_started", service="version-manager", port=8001)
    yield


app = FastAPI(title="Version Manager", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def count_requests(request: Request, call_next):
    _METRICS["requests_total"] += 1
    return await call_next(request)


@app.exception_handler(VersionError)
async def version_error_handler(request: Request, exc: VersionError):
    return JSONResponse(status_code=exc.status_code, content=err(exc.code, exc.message))


app.include_router(versions_router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    lines = [
        "# HELP version_manager_requests_total Total HTTP requests.",
        "# TYPE version_manager_requests_total counter",
        f"version_manager_requests_total {_METRICS['requests_total']}",
    ]
    return "\n".join(lines) + "\n"


@app.get("/")
def root():
    return {"data": {"service": "01-version-manager", "version": "1.0.0"}, "error": None}
