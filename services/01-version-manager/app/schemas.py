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


class CompareRequest(BaseModel):
    """대조할 버전 목록. 마스터/배포본이 섞여 있어도 된다."""

    version_ids: list[str]


class MergeRequest(BaseModel):
    """행 단위 채택 결과 → 최종 취합본.

    selections: {지원자 번호: 채택할 version_id}. 빠진 지원자는 기준 버전을 쓴다.
    """

    round_id: str
    base_version_id: str
    version_ids: list[str] = []
    selections: dict[str, str] = {}
    actor: str = "console"
    file_name: str | None = None
