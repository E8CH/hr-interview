"""재편성 대안(Plan) 도메인 모델"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

PlanType = Literal["A_safe", "B_defer", "C_cross_team"]

PLAN_DESCRIPTIONS: dict[str, str] = {
    "A_safe": "예비 슬롯 활용 · 팀 일치",
    "B_defer": "이번 회차 유지 · 다음 회차 이월",
    "C_cross_team": "cross-team 재예약 허용",
}


class SlotRef(BaseModel):
    """면접 슬롯 좌표 (날 × 시간 × 면접위원)"""
    day: str
    hour: str
    interviewer_id: str
    team: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.day, self.hour, self.interviewer_id)


class RepairChange(BaseModel):
    """Plan 이 적용되면 발생할 배정 변경 1건"""
    applicant_id: str
    action: Literal["rebook", "defer"]
    reason: str
    from_slot: SlotRef | None = None
    to_slot: SlotRef | None = None
    team_match: bool = True
    lock_level: str = "DRAFT"


class RepairPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    event_id: str
    plan_type: PlanType
    rebooked_count: int = 0
    deferred_count: int = 0
    hard_violations: int = 0
    soft_penalty: int = 0
    cross_team_count: int = 0
    description: str = ""
    warning: str | None = None
    changes: list[RepairChange] = []
    reopened_slots: list[SlotRef] = []
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    def to_detail(self) -> dict:
        """repair_plans.plan_detail 에 저장할 JSON"""
        return {
            "changes": [c.model_dump(mode="json") for c in self.changes],
            "reopened_slots": [s.model_dump(mode="json") for s in self.reopened_slots],
            "cross_team_count": self.cross_team_count,
            "description": self.description,
            "warning": self.warning,
        }

    def to_summary(self) -> dict:
        """GET /plans/{event_id} 응답 형태"""
        summary = {
            "plan_id": self.plan_id,
            "type": self.plan_type,
            "rebooked": self.rebooked_count,
            "deferred": self.deferred_count,
            "hard": self.hard_violations,
            "soft": self.soft_penalty,
            "description": self.description,
        }
        if self.plan_type == "C_cross_team":
            summary["cross_team_count"] = self.cross_team_count
        if self.warning:
            summary["warning"] = self.warning
        return summary
