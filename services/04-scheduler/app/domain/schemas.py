"""API 요청/응답 Pydantic 스키마 + 배치 알고리즘 입력 DTO"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.infrastructure.contracts import DAYS, HOURS

LOCK_ORDER = ["DRAFT", "CONFIRMED", "LOCKED"]


# --------------------------------------------------------------------------
# 알고리즘 입력 DTO (ORM/HTTP와 분리된 순수 값 객체)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ApplicantIn:
    applicant_id: str
    name: str
    team: str
    degree: str  # 학사 | 대학원
    priority_score: float = 0.0
    target_lab: bool = False
    tags: tuple[str, ...] = ()

    @property
    def is_grad(self) -> bool:
        return self.degree == "대학원"


@dataclass(frozen=True)
class InterviewerIn:
    interviewer_id: str
    name: str
    team: str
    title: str = ""          # 직급
    max_daily: int = 6
    priority: int = 2
    email: str = ""
    availability: dict[str, list[str]] = field(default_factory=dict)

    def is_available(self, day: str, hour: str) -> bool:
        return hour in self.availability.get(day, [])


@dataclass
class PlannedAssignment:
    applicant_id: str
    applicant_name: str
    interviewer_id: str
    team: str
    degree: str
    day: str
    hour: str
    reason_tags: list[str] = field(default_factory=list)
    lock_level: str = "DRAFT"


@dataclass
class PlanResult:
    algorithm: str
    assignments: list[PlannedAssignment]
    unassigned: list[ApplicantIn]
    notes: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# REST 스키마
# --------------------------------------------------------------------------
class GenerateConstraints(BaseModel):
    grad_ratio_target: float = 0.30
    grad_ratio_tolerance: float = 0.20
    max_daily_default: int = 6
    # 부서가 확정한 짝 (지원자 사번 → 담당자 사번). 여기 있는 지원자는
    # 이 담당자에게만 붙는다. 시간표는 시간만 정한다.
    pairs: dict[str, str] = Field(default_factory=dict)
    # 팀별 짝 {팀: {지원자 사번: 담당자 사번}}. 두 팀이 같이 보는 사람은 팀마다
    # 담당자가 다르므로 지원자 사번 하나로는 담을 수 없다 — 그 팀 자리에는
    # 여기 적힌 담당자를 쓰고, 팀이 여기 없으면 위 pairs 로 되돌아간다.
    pairs_by_team: dict[str, dict[str, str]] = Field(default_factory=dict)


class GenerateRequest(BaseModel):
    round_id: str
    plan_id: str
    algorithm: str = "v5"
    constraints: GenerateConstraints = Field(default_factory=GenerateConstraints)
    generated_by: str = "system"
    # None = 짝 개념 없이 예전처럼 자유 배정(테스트 · 이전 호출자용).
    # {} = 부서가 아직 아무도 매칭하지 않았다 → 배치할 사람이 없다.
    pairs: dict[str, str] | None = None
    # 팀별 짝 {팀: {지원자: 담당자}} — 두 팀이 같이 보는 사람 때문에 필요하다.
    # 주면 그 팀 자리는 여기를 따르고, 여기 없는 팀만 pairs 를 본다.
    pairs_by_team: dict[str, dict[str, str]] | None = None

    @field_validator("algorithm")
    @classmethod
    def _known_algorithm(cls, v: str) -> str:
        from app.services.registry import normalize_algorithm

        return normalize_algorithm(v)


class LockRequest(BaseModel):
    lock_level: Literal["DRAFT", "CONFIRMED", "LOCKED"]
    applicant_ids: list[str] | None = None
    actor: str = "system"


class InterviewerCreate(BaseModel):
    interviewer_id: str
    name: str = ""
    title: str = ""          # 직급 (책임 · 선임 …) — 동명이인을 가르는 표시
    team: str
    max_daily: int = 6
    priority: int = 2
    email: str = ""
    backup_email: str | None = None
    availability: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("availability")
    @classmethod
    def _valid_availability(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for day, hours in v.items():
            if day not in DAYS:
                raise ValueError(f"알 수 없는 요일: {day}")
            for hour in hours:
                if hour not in HOURS:
                    raise ValueError(f"알 수 없는 시간대: {hour}")
        return v


class InterviewerUpdate(BaseModel):
    name: str | None = None
    title: str | None = None
    team: str | None = None
    max_daily: int | None = None
    priority: int | None = None
    email: str | None = None
    backup_email: str | None = None
    availability: dict[str, list[str]] | None = None

    @field_validator("availability")
    @classmethod
    def _valid_availability(cls, v: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        if v is None:
            return v
        return InterviewerCreate._valid_availability(v)


class RoundSelectionIn(BaseModel):
    """회차에 투입할 면접관 선별 — 넘긴 목록으로 통째로 교체한다."""

    interviewer_ids: list[str] = Field(default_factory=list)
    actor: str = "console"


class InterviewerBandsIn(BaseModel):
    """사번 → 가능 시간(오전·오후 / 오전만 / 오후만 / 어려움) 일괄 저장."""

    bands: dict[str, str] = Field(default_factory=dict)
    actor: str = "console"


class AssignmentOut(BaseModel):
    assignment_id: str
    applicant_id: str
    applicant_name: str = ""
    interviewer_id: str
    team: str = ""
    degree: str = ""
    day: str
    hour: str
    lock_level: str = "DRAFT"
    reason_tags: list[str] = []
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
