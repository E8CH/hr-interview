"""API 요청/응답 Pydantic 스키마 + 배치 알고리즘 입력 DTO"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.infrastructure.contracts import HOURS

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
    max_daily: int = len(HOURS)
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
class DeptSeat(BaseModel):
    """부서 시간표에서의 자리 — 며칠째(1부터) 몇 번째 칸(0부터).

    부서 화면은 하루 몇 칸씩 끊어 세기만 한다. 그 일차가 이 팀 면접일 중
    몇째 날인지는 인사팀이 정하므로 여기서는 일차 번호로만 주고받는다.
    """
    day: int = Field(ge=1)
    slot: int = Field(ge=0)


class GenerateConstraints(BaseModel):
    grad_ratio_target: float = 0.30
    grad_ratio_tolerance: float = 0.20
    max_daily_default: int = len(HOURS)
    # 담당자가 적어 낸 가능 시간을 무시하고 자리부터 채운다. 앞타임만 되는
    # 사람만 남아 늦은 자리가 비는 것을 인사가 감수하기로 했을 때만 켠다.
    ignore_availability: bool = False
    # 부서가 확정한 짝 (지원자 사번 → 담당자 사번). 여기 있는 지원자는
    # 이 담당자에게만 붙는다. 시간표는 시간만 정한다.
    pairs: dict[str, str] = Field(default_factory=dict)
    # 팀별 짝 {팀: {지원자 사번: 담당자 사번}}. 두 팀이 같이 보는 사람은 팀마다
    # 담당자가 다르므로 지원자 사번 하나로는 담을 수 없다 — 그 팀 자리에는
    # 여기 적힌 담당자를 쓰고, 팀이 여기 없으면 위 pairs 로 되돌아간다.
    pairs_by_team: dict[str, dict[str, str]] = Field(default_factory=dict)
    # 부서가 자기 시간표에서 잡아 둔 자리 {팀: {지원자: 자리}}. 최종 시간표는
    # 이 자리를 먼저 지키고, 못 지킬 때만 옮기면서 그 까닭을 사유로 남긴다.
    seats_by_team: dict[str, dict[str, DeptSeat]] = Field(default_factory=dict)
    # 인사가 명단을 보낼 때 이미 정해 둔 팀별 면접일 {팀: ["1일차", "2일차"]}.
    # 부서는 이 날들을 보고 자리를 잡았으므로, 여기서 다시 뽑으면 부서가 본
    # 시간표와 어긋난다. 안 주면 예전처럼 새로 뽑는다.
    days_by_team: dict[str, list[str]] = Field(default_factory=dict)

    def seat_map(self) -> dict[str, dict[str, tuple[int, int]]]:
        """Board 가 쓰는 모양 {팀: {지원자: (일차, 칸 번호)}} 로 바꾼다."""
        return {
            team: {a: (seat.day, seat.slot) for a, seat in (wish or {}).items()}
            for team, wish in self.seats_by_team.items()
        }


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
    # 부서가 자기 시간표에서 잡아 둔 자리 {팀: {지원자: {"day": n, "slot": i}}}.
    # 안 주면 예전처럼 시간표가 날 · 시각을 처음부터 새로 짠다.
    seats_by_team: dict[str, dict[str, DeptSeat]] | None = None
    # 인사가 3단계에서 못 박은 팀별 면접일 {팀: ["1일차", "2일차"]}.
    days_by_team: dict[str, list[str]] | None = None

    @field_validator("algorithm")
    @classmethod
    def _known_algorithm(cls, v: str) -> str:
        from app.services.registry import normalize_algorithm

        return normalize_algorithm(v)


class FixDuplicatesRequest(BaseModel):
    """중복면접자만 다시 앉히기 — 겹친 사람 외에는 자리를 건드리지 않는다.

    팀별 면접일을 함께 주면 옮길 칸을 그 날들 안에서만 찾는다. 안 주면
    닷새 전부에서 찾으므로, 그 팀이 안 보는 날로 넘어갈 수 있다.
    """

    days_by_team: dict[str, list[str]] | None = None
    actor: str = "system"


class LockRequest(BaseModel):
    lock_level: Literal["DRAFT", "CONFIRMED", "LOCKED"]
    applicant_ids: list[str] | None = None
    actor: str = "system"


class InterviewerCreate(BaseModel):
    interviewer_id: str
    name: str = ""
    title: str = ""          # 직급 (책임 · 선임 …) — 동명이인을 가르는 표시
    team: str
    max_daily: int = len(HOURS)
    priority: int = 2
    email: str = ""
    backup_email: str | None = None
    availability: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("availability")
    @classmethod
    def _valid_availability(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        # 날 이름은 따지지 않는다 — 읽을 때 `normalize_availability()` 가 지우고
        # 모든 날에 똑같이 펴기 때문이다. 자료에 어떤 이름이 키로 남아 있든 뜻을
        # 갖지 않으므로, 여기서 물려 봐야 회차만 못 열게 될 뿐이다.
        for hours in v.values():
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
    """사번 → 가능 시간(모든타임 / 앞타임 / 뒤타임 / 어려움) 일괄 저장.

    어느 칸이 앞타임이고 어느 칸이 뒤타임인지는 면접 진행 조건(시작 시각 ·
    면접 분 · 쉬는 분)에 따라 달라진다. 그래서 그 조건을 함께 받아 칸으로
    펼친다. 두 덩어리는 12시 ~ 14시에서 겹치므로 그 사이의 칸은 양쪽 다 맡을
    수 있다. 예전 표기('오전만' · '둘 다' 등)로 보내도 지금 이름으로 옮겨 받는다.

    **어느 날인지는 받지 않는다.** 고른 덩어리는 1일차든 3일차든 똑같이 적용된다.
    """

    bands: dict[str, str] = Field(default_factory=dict)
    #: 3단계에서 정한 면접 진행 조건. 안 주면 기본값으로 계산한다.
    timing: dict[str, Any] | None = None
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
