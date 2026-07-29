"""API 요청/응답 스키마 (Pydantic v2)"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


# --- 공통 응답 봉투 (00_SHARED_CONTRACT §5) ---
class ErrorBody(BaseModel):
    code: str
    message: str


class Envelope(BaseModel, Generic[T]):
    data: T | None = None
    error: ErrorBody | None = None


def ok(data: Any) -> dict:
    return {"data": data, "error": None}


def fail(code: str, message: str) -> dict:
    return {"data": None, "error": {"code": code, "message": message}}


# --- 요청 발송 ---
class InviteeIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=255)
    team: str = Field(min_length=1, max_length=64)
    org: str | None = Field(default=None, max_length=64)
    dept_leader_email: str | None = Field(default=None, max_length=255)


class CreateRequestIn(BaseModel):
    round_id: str = Field(min_length=1, max_length=32)
    plan_id: str
    deadline: datetime
    invitees: list[InviteeIn] = Field(min_length=1)
    team_name: str | None = None
    correlation_id: str | None = None


class CreateRequestOut(BaseModel):
    request_id: str
    sent_count: int


# --- 폼 응답 ---
class SlotIn(BaseModel):
    day: str
    hour: str


class FormSubmitIn(BaseModel):
    """폼 제출 payload — 상세 검증은 `services.validator` 가 담당."""

    job_role: str
    available_slots: list[SlotIn]
    max_daily: int | None = None
    backup_contact: str | None = None
    notes: str | None = None


class FormSubmitOut(BaseModel):
    response_id: str
    invitee_id: str
    submitted_at: datetime
    validated: bool
    slot_count: int
    response_hours: float | None = None


# --- 회신 현황 ---
class ResponseItem(BaseModel):
    invitee_id: str
    name: str
    email: str
    team: str
    org: str | None
    responded: bool
    submitted_at: datetime | None = None
    response_hours: float | None = None
    last_reminder_level: int = 0
    payload: dict | None = None


class ResponseSummary(BaseModel):
    round_id: str
    total: int
    responded: int
    pending: int
    response_rate: float
    avg_response_hours: float | None
    responses: list[ResponseItem]


# --- 조직 패턴 ---
class OrgPatternOut(BaseModel):
    org: str
    mean_hours: float
    std_hours: float
    sample_count: int
    predicted_slow: bool
    updated_at: datetime | None = None


# --- 리마인더 ---
class ReminderTriggerIn(BaseModel):
    invitee_id: str
    level: int = Field(ge=1, le=3)
    channel: str = "email"
    force: bool = False


class ReminderTriggerOut(BaseModel):
    sent: bool
    invitee_id: str
    level: int | None = None
    cc_supervisor: bool = False
    escalated: bool = False
    reason: str
