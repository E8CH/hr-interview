"""Service: Distributor
포트: 8002
명세: bmad/02_distributor.md
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import export, plans, profiles
from app.config import settings
from app.events import register_subscribers
from app.infrastructure.db import (
    AssignmentReasonORM,
    DistributionPlanORM,
    init_db,
    new_session,
)
from app.services.plan_service import ServiceError

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    register_subscribers()
    logger.info("distributor 기동 완료 (mock=%s, db=%s)", settings.use_mock, settings.database_url)
    yield


app = FastAPI(
    title="Distributor",
    version="1.0.0",
    description="Service 02 — 팀 프로필 기반 자동 배포 엔진 (6축 스코어링 · 사유 태그 · 중복 관리)",
    lifespan=lifespan,
)

app.include_router(plans.router)
app.include_router(profiles.router)
app.include_router(export.router)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"data": None, "error": {"code": code, "message": message}},
    )


@app.exception_handler(ServiceError)
async def _service_error_handler(_request: Request, exc: ServiceError):
    return _error(exc.status_code, exc.code, exc.message)


@app.exception_handler(StarletteHTTPException)
async def _http_error_handler(_request: Request, exc: StarletteHTTPException):
    code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
    return _error(exc.status_code, code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(_request: Request, exc: RequestValidationError):
    return _error(422, "VALIDATION_FAILED", str(exc.errors()))


@app.get("/healthz", tags=["ops"])
def healthz():
    return {"status": "ok", "service": "02-distributor"}


@app.get("/metrics", response_class=PlainTextResponse, tags=["ops"])
def metrics():
    with new_session() as session:
        plans_total = session.scalar(select(func.count()).select_from(DistributionPlanORM)) or 0
        approved = (
            session.scalar(
                select(func.count())
                .select_from(DistributionPlanORM)
                .where(DistributionPlanORM.status == "approved")
            )
            or 0
        )
        assignments = session.scalar(select(func.count()).select_from(AssignmentReasonORM)) or 0
        duplicates = (
            session.scalar(
                select(func.count())
                .select_from(AssignmentReasonORM)
                .where(AssignmentReasonORM.is_duplicate.is_(True))
            )
            or 0
        )
    lines = [
        "# HELP distributor_plans_total 생성된 배포안 수",
        "# TYPE distributor_plans_total counter",
        f"distributor_plans_total {plans_total}",
        "# HELP distributor_plans_approved_total 승인된 배포안 수",
        "# TYPE distributor_plans_approved_total counter",
        f"distributor_plans_approved_total {approved}",
        "# HELP distributor_assignments_total 저장된 배정 수",
        "# TYPE distributor_assignments_total counter",
        f"distributor_assignments_total {assignments}",
        "# HELP distributor_duplicate_assignments_total 중복 배포 수",
        "# TYPE distributor_duplicate_assignments_total counter",
        f"distributor_duplicate_assignments_total {duplicates}",
    ]
    return "\n".join(lines) + "\n"


@app.get("/", tags=["ops"])
def root():
    return {"data": {"service": "02-distributor", "version": "1.0.0"}, "error": None}
