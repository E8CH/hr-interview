"""공통 에러 규약 — {"data": null, "error": {"code","message"}}"""
from __future__ import annotations


class ApiError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail or {}

    def body(self) -> dict:
        error = {"code": self.code, "message": self.message}
        if self.detail:
            error["detail"] = self.detail
        return {"data": None, "error": error}


class NotFoundError(ApiError):
    def __init__(self, message: str, detail: dict | None = None):
        super().__init__("NOT_FOUND", message, 404, detail)


class ValidationFailed(ApiError):
    def __init__(self, message: str, detail: dict | None = None):
        super().__init__("VALIDATION_FAILED", message, 400, detail)


class ConflictError(ApiError):
    def __init__(self, message: str, detail: dict | None = None):
        super().__init__("CONFLICT", message, 409, detail)


def ok(data) -> dict:
    return {"data": data, "error": None}
