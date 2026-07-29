"""요청 · 응답 스키마 (공통 REST 규약: {"data": ..., "error": null})"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


def ok(data: Any) -> dict:
    return {"data": data, "error": None}


def fail(code: str, message: str) -> dict:
    return {"data": None, "error": {"code": code, "message": message}}


class NoshowRequest(BaseModel):
    round_id: str
    schedule_id: str
    noshow_applicant_ids: list[str] = Field(min_length=1)
    reported_by: str = "system"


class CancelRequest(BaseModel):
    round_id: str
    schedule_id: str
    cancel_type: Literal["applicant", "interviewer"]
    target_id: str
    reason: str = ""


class SelectPlanRequest(BaseModel):
    plan_id: str
    selected_by: str = "system"


class LockUpgradeRequest(BaseModel):
    schedule_id: str
    applicant_ids: list[str] = Field(min_length=1)
    new_level: str
