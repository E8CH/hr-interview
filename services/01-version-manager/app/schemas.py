"""API 스키마 — 요청/응답. 공통 응답봉투 {data, error}."""
from typing import Any

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str


class Envelope(BaseModel):
    data: Any = None
    error: ErrorBody | None = None


def ok(data: Any) -> dict:
    return {"data": data, "error": None}


def err(code: str, message: str) -> dict:
    return {"data": None, "error": {"code": code, "message": message}}


class RollbackRequest(BaseModel):
    version_id: str
