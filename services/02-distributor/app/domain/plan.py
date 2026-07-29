"""배포안 도메인 모델 · API 요청/응답 스키마."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PlanStatus = Literal["draft", "approved", "rejected", "adjusted"]


class Assignment(BaseModel):
    """지원자 1명 → 팀 1개 배정 결과 (assignment_reasons 1행)."""

    applicant_id: str
    team_name: str
    score: float
    tags: list[str] = Field(default_factory=list)
    is_duplicate: bool = False
    primary_team: str | None = None


class PlanSummary(BaseModel):
    plan_id: str
    round_id: str
    status: PlanStatus
    master_version_id: str
    total_applicants: int
    team_counts: dict[str, int]
    duplicate_count: int
    created_by: str | None = None
    created_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None
    # 생성 시점에만 채워지는 진단 정보
    elapsed_seconds: float | None = None
    unassigned: list[str] = Field(default_factory=list)
    filtered_count: int | None = None


class CreatePlanRequest(BaseModel):
    round_id: str
    master_version_id: str
    allow_duplicate: bool = True
    duplicate_score_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    created_by: str | None = None


class ApproveRequest(BaseModel):
    actor: str


class RejectRequest(BaseModel):
    reason: str


class Move(BaseModel):
    applicant_id: str
    from_: str = Field(alias="from")
    to: str
    reason: str | None = None

    model_config = {"populate_by_name": True}


class AdjustRequest(BaseModel):
    moves: list[Move]
    actor: str | None = None
