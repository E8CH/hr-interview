"""공통 에러 타입 · 핸들러 (00_SHARED_CONTRACT §5 응답 규약)"""
from __future__ import annotations

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas import fail

logger = structlog.get_logger(__name__)


class ApiError(Exception):
    """도메인 에러 — 공통 에러 봉투로 직렬화된다."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "BAD_REQUEST"

    def __init__(self, message: str, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFound(ApiError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class ValidationFailed(ApiError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "VALIDATION_FAILED"


class Conflict(ApiError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content=fail(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        detail = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'][1:])}: {err['msg']}" for err in exc.errors()
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=fail("VALIDATION_FAILED", detail or "요청 형식 오류"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(
            status_code=exc.status_code, content=fail(code, str(exc.detail))
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # pragma: no cover - 안전망
        logger.error("unhandled_error", path=str(request.url), error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=fail("INTERNAL_ERROR", "내부 오류가 발생했습니다."),
        )
