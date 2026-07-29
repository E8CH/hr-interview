"""공통 REST 응답 규약 (00_SHARED_CONTRACT.md §5)

    성공: {"data": ..., "error": null}
    실패: {"data": null, "error": {"code": "...", "message": "..."}}
"""
from __future__ import annotations

from typing import Any


class ErrorCode:
    VALIDATION_FAILED = "VALIDATION_FAILED"
    NOT_FOUND = "NOT_FOUND"
    INTEGRITY_VIOLATION = "INTEGRITY_VIOLATION"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ApiError(Exception):
    """도메인 에러 — main.py의 예외 핸들러가 공통 봉투로 렌더링한다."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def ok(data: Any) -> dict:
    return {"data": data, "error": None}


def fail(code: str, message: str) -> dict:
    return {"data": None, "error": {"code": code, "message": message}}
