"""배포안 도메인 모델 · API 요청/응답 스키마."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PlanStatus = Literal["draft", "approved", "rejected", "adjusted"]

#: 배정안을 무엇으로 만들었는지.
#: - inherit: 1단계에서 팀이 적어 낸 '담당팀' 을 그대로 옮긴다 (기본)
#: - auto:    점수로 5팀에 새로 섞는다 (명단 재배치)
PlanMode = Literal["inherit", "auto"]
MODE_INHERIT: PlanMode = "inherit"
MODE_AUTO: PlanMode = "auto"


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
    mode: PlanMode = MODE_AUTO
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
    #: 승계에서 '담당팀' 에 있었지만 아는 팀이 아니었던 이름 — 오타를 여기서 잡는다
    unknown_teams: list[str] = Field(default_factory=list)
    #: 승계에서 담당팀이 비어 있어 점수로 나눠 담은 인원
    auto_filled: int = 0


class CreatePlanRequest(BaseModel):
    round_id: str
    master_version_id: str
    #: 화면에서는 승계가 기본이지만 여기 기본값은 재배치다 — 담당팀이 없던 시절의
    #: 취합파일로 부르는 백테스트가 그대로 돌아야 한다. 승계는 콘솔이 명시적으로 건다.
    #: 아래 두 값(중복 허용·기준 점수)은 재배치에서만 쓰인다.
    mode: PlanMode = MODE_AUTO
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
