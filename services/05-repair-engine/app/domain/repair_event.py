"""재편성 트리거 이벤트 도메인 모델"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

TriggerType = Literal["noshow", "cancel_applicant", "cancel_interviewer", "change"]
EventStatus = Literal["pending", "resolved", "deferred"]

# 3단계 락 — 값이 클수록 강하게 고정된다.
LOCK_LEVELS: dict[str, int] = {"DRAFT": 0, "CONFIRMED": 1, "LOCKED": 2}

#: CONFIRMED 배정을 옮길 때 부여하는 소프트 페널티
CONFIRMED_MOVE_PENALTY = 10


def lock_rank(level: str | None) -> int:
    """알 수 없는 락 레벨은 가장 약한 DRAFT 로 간주한다."""
    return LOCK_LEVELS.get((level or "DRAFT").upper(), 0)


def is_immovable(level: str | None) -> bool:
    """LOCKED 는 어떤 Plan 에서도 이동 불가."""
    return lock_rank(level) >= LOCK_LEVELS["LOCKED"]


class RepairEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    round_id: str
    schedule_id: str
    trigger_type: TriggerType
    trigger_target: str
    reported_at: datetime = Field(default_factory=datetime.utcnow)
    reported_by: str
    status: EventStatus = "pending"
    #: 이 이벤트로 인해 자리를 잃은 지원자들
    affected_applicant_ids: list[str] = []
    correlation_id: str = Field(default_factory=lambda: str(uuid4()))
