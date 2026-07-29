"""3단계 락 시스템

DRAFT     자유롭게 재편성 가능
CONFIRMED 면접관 안내 발송 완료 — 재편성 시 페널티
LOCKED    지원자 안내 발송 완료 — 재편성 절대 금지

상승은 반드시 한 단계씩(DRAFT → CONFIRMED → LOCKED)만 허용한다.
강등·단계 건너뛰기·LOCKED 이후 변경은 모두 거부.
"""
from __future__ import annotations

from dataclasses import dataclass

LOCK_ORDER: list[str] = ["DRAFT", "CONFIRMED", "LOCKED"]
LOCK_RANK: dict[str, int] = {name: idx for idx, name in enumerate(LOCK_ORDER)}


class LockTransitionError(ValueError):
    """허용되지 않은 락 전이"""

    def __init__(self, message: str, current: str | None = None, requested: str | None = None):
        super().__init__(message)
        self.current = current
        self.requested = requested


@dataclass
class LockDecision:
    current: str
    requested: str
    allowed: bool
    reason: str = ""


def rank(level: str) -> int:
    if level not in LOCK_RANK:
        raise LockTransitionError(f"알 수 없는 락 레벨: {level}", requested=level)
    return LOCK_RANK[level]


def can_transition(current: str, requested: str) -> LockDecision:
    cur, req = rank(current), rank(requested)
    if req == cur:
        return LockDecision(current, requested, False, f"이미 {current} 상태입니다")
    if req < cur:
        return LockDecision(current, requested, False, f"락 강등 불가: {current} → {requested}")
    if req - cur > 1:
        return LockDecision(
            current,
            requested,
            False,
            f"락 단계는 한 단계씩만 상승 가능: {current} → {LOCK_ORDER[cur + 1]} 순서를 지키세요",
        )
    return LockDecision(current, requested, True)


def assert_transition(current: str, requested: str) -> None:
    decision = can_transition(current, requested)
    if not decision.allowed:
        raise LockTransitionError(decision.reason, current=current, requested=requested)


def is_reassignable(level: str) -> bool:
    """재편성(Service 05 repair) 가능 여부"""
    return rank(level) < LOCK_RANK["LOCKED"]


def schedule_status_for(levels: list[str]) -> str:
    """배정들의 락 레벨로 스케줄 전체 상태를 산출 (가장 낮은 단계 기준)"""
    if not levels:
        return "draft"
    lowest = min(rank(level) for level in levels)
    return LOCK_ORDER[lowest].lower()


def apply_lock(
    assignments: list, requested: str, applicant_ids: list[str] | None = None
) -> list:
    """대상 배정들의 락 레벨을 한 단계 상승시키고 변경된 목록을 반환.

    applicant_ids가 주어지면 해당 지원자 건만 대상으로 한다.
    대상 중 하나라도 전이 불가면 아무것도 바꾸지 않고 예외를 던진다(원자성).
    """
    targets = assignments
    if applicant_ids is not None:
        wanted = set(applicant_ids)
        targets = [a for a in assignments if a.applicant_id in wanted]
        missing = wanted - {a.applicant_id for a in assignments}
        if missing:
            raise LockTransitionError(
                f"스케줄에 없는 지원자: {', '.join(sorted(missing))}", requested=requested
            )
    if not targets:
        raise LockTransitionError("락을 적용할 배정이 없습니다", requested=requested)

    for assignment in targets:
        assert_transition(assignment.lock_level, requested)

    for assignment in targets:
        assignment.lock_level = requested
    return targets
