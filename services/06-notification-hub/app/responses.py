"""공통 REST 응답 규약 — {"data": ..., "error": null}"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class ApiError(HTTPException):
    """에러 코드(대문자 스네이크)를 담는 예외."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message


class NotFoundError(ApiError):
    def __init__(self, message: str) -> None:
        super().__init__("NOT_FOUND", message, status_code=404)


class ValidationFailed(ApiError):
    def __init__(self, message: str) -> None:
        super().__init__("VALIDATION_FAILED", message, status_code=422)


def ok(data: Any) -> dict:
    return {"data": data, "error": None}


def fail(code: str, message: str) -> dict:
    return {"data": None, "error": {"code": code, "message": message}}
