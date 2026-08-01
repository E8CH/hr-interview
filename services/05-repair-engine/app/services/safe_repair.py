"""안전 재편성 알고리즘 (v3.1)

    1. LOCKED 상태 대상자는 즉시 다음 회차 이월 (절대 이동 불가)
    2. 나머지 대상자에게 예비 슬롯 재예약 시도
    3. 각 후보 슬롯마다 하드 제약 재검증
    4. 위반이 생기는 슬롯은 스킵
    5. 팀 일치 우선, 실패 시 deferred

v3 대비 핵심 차이는 3번이다. 재검증 없이 슬롯을 채우면 규칙2(같은 팀 동시간)
위반이 발생한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shared.contracts.constants import DAYS, HOURS

from app.domain.repair_event import CONFIRMED_MOVE_PENALTY, is_immovable, lock_rank
from app.domain.repair_plan import RepairChange, SlotRef
from app.domain.schedule import ScheduleAssignment, ScheduleSnapshot
from app.services.constraint_recheck import (ConstraintIndex,
                                             check_hard_constraints,
                                             compute_soft_penalty)

DAY_INDEX = {d: i for i, d in enumerate(DAYS)}
HOUR_INDEX = {h: i for i, h in enumerate(HOURS)}


def _slot_sort_key(slot: SlotRef) -> tuple:
    return (DAY_INDEX.get(slot.day, 99), HOUR_INDEX.get(slot.hour, 99), slot.interviewer_id)


@dataclass
class RepairOutcome:
    changes: list[RepairChange] = field(default_factory=list)
    assignments: list[ScheduleAssignment] = field(default_factory=list)
    reopened_slots: list[SlotRef] = field(default_factory=list)
    unused_reserved: list[SlotRef] = field(default_factory=list)
    hard_violations: int = 0
    violation_details: list[dict] = field(default_factory=list)
    soft_penalty: int = 0
    cross_team_count: int = 0

    @property
    def rebooked_count(self) -> int:
        return sum(1 for c in self.changes if c.action == "rebook")

    @property
    def deferred_count(self) -> int:
        return sum(1 for c in self.changes if c.action == "defer")

    @property
    def deferred_applicant_ids(self) -> list[str]:
        return [c.applicant_id for c in self.changes if c.action == "defer"]


def repair_safely(snapshot: ScheduleSnapshot,
                  affected_applicant_ids: list[str],
                  lock_overrides: dict[str, str] | None = None,
                  *,
                  allow_cross_team: bool = False,
                  defer_all: bool = False,
                  excluded_interviewers: set[str] | None = None) -> RepairOutcome:
    """`affected_applicant_ids` 를 안전하게 재편성한다.

    영향받지 않은 배정은 절대 건드리지 않는다 (LOCKED/CONFIRMED 포함).
    """
    lock_overrides = lock_overrides or {}
    excluded_interviewers = excluded_interviewers or set()

    iv_map = snapshot.interviewer_map()
    ap_map = snapshot.applicant_map()

    affected = sorted(set(affected_applicant_ids))
    affected_set = set(affected)

    # 1) 대상자의 기존 배정을 떼어낸다 → 그 슬롯은 반환(SLOT_REOPENED) 대상
    kept: list[ScheduleAssignment] = []
    removed: dict[str, ScheduleAssignment] = {}
    for a in snapshot.assignments:
        if a.applicant_id in affected_set and a.interviewer_id not in excluded_interviewers:
            removed[a.applicant_id] = a
        elif a.interviewer_id in excluded_interviewers:
            removed[a.applicant_id] = a
        else:
            kept.append(a)

    reopened = [a.slot for a in removed.values()]

    outcome = RepairOutcome(reopened_slots=sorted(reopened, key=_slot_sort_key))

    index = ConstraintIndex.build(kept, snapshot.interviewers)

    # 사용 가능한 예비 슬롯 (취소된 면접위원의 슬롯은 제외)
    available: list[SlotRef] = [s for s in snapshot.reserved_slots
                                if s.interviewer_id not in excluded_interviewers]
    available.sort(key=_slot_sort_key)
    used_slots: set[tuple[str, str, str]] = set()

    move_penalty = 0
    new_assignments: list[ScheduleAssignment] = []

    for applicant_id in affected:
        original = removed.get(applicant_id)
        effective_lock = lock_overrides.get(
            applicant_id, original.lock_level if original else "DRAFT")
        applicant = ap_map.get(applicant_id)
        home_team = applicant.team_1st if applicant else (original.team if original else "")
        from_slot = original.slot if original else None

        # --- Plan B: 전원 이월 ---
        if defer_all:
            outcome.changes.append(RepairChange(
                applicant_id=applicant_id, action="defer",
                reason="PLAN_B_DEFER_ALL", from_slot=from_slot,
                lock_level=effective_lock))
            continue

        # --- 1) LOCKED 는 즉시 이월 ---
        if is_immovable(effective_lock):
            outcome.changes.append(RepairChange(
                applicant_id=applicant_id, action="defer",
                reason="LOCKED_IMMOVABLE", from_slot=from_slot,
                lock_level=effective_lock))
            continue

        # --- 2) 후보 슬롯: 팀 일치 우선 ---
        same_team = [s for s in available
                     if s.team == home_team and s.key not in used_slots]
        candidates = list(same_team)
        if allow_cross_team:
            candidates += [s for s in available
                           if s.team != home_team and s.key not in used_slots]

        placed: SlotRef | None = None
        for slot in candidates:
            # --- 3) 하드 제약 재검증 · 4) 위반 슬롯은 스킵 ---
            if not index.is_safe(applicant_id, slot.day, slot.hour,
                                 slot.interviewer_id, slot.team):
                continue
            placed = slot
            break

        if placed is None:
            # --- 5) 안전한 슬롯이 없으면 이월 ---
            outcome.changes.append(RepairChange(
                applicant_id=applicant_id, action="defer",
                reason="NO_SAFE_SLOT", from_slot=from_slot,
                lock_level=effective_lock))
            continue

        new_assignment = ScheduleAssignment(
            assignment_id=f"RP-{applicant_id}-{placed.day}{placed.hour}",
            applicant_id=applicant_id,
            interviewer_id=placed.interviewer_id,
            day=placed.day, hour=placed.hour, team=placed.team,
            lock_level=effective_lock,
            reason_tags=(original.reason_tags if original else []) + ["HR_MANUAL"],
        )
        index.add(new_assignment)
        new_assignments.append(new_assignment)
        used_slots.add(placed.key)

        team_match = placed.team == home_team
        if not team_match:
            outcome.cross_team_count += 1
        if lock_rank(effective_lock) == 1:      # CONFIRMED 이동 → 페널티
            move_penalty += CONFIRMED_MOVE_PENALTY

        outcome.changes.append(RepairChange(
            applicant_id=applicant_id, action="rebook",
            reason="SAME_TEAM_RESERVED" if team_match else "CROSS_TEAM_RESERVED",
            from_slot=from_slot, to_slot=placed, team_match=team_match,
            lock_level=effective_lock))

    result = kept + new_assignments

    # --- 최종 전수 검증: 위반이 남아 있으면 해당 재예약을 철회하고 이월 처리 ---
    violations = check_hard_constraints(result, snapshot.interviewers)
    if violations:
        result, outcome = _rollback_unsafe(result, new_assignments, kept,
                                           outcome, snapshot)
        violations = check_hard_constraints(result, snapshot.interviewers)

    outcome.assignments = result
    outcome.hard_violations = len(violations)
    outcome.violation_details = [v.as_dict() for v in violations]
    outcome.soft_penalty = compute_soft_penalty(
        result, snapshot.interviewers, snapshot.applicants,
        snapshot.timing) + move_penalty
    outcome.unused_reserved = [s for s in available if s.key not in used_slots]

    # 영향받지 않은 배정은 그대로 유지되어야 한다 (LOCKED 불변 보장)
    _assert_untouched(kept, result)
    return outcome


def _rollback_unsafe(result: list[ScheduleAssignment],
                     new_assignments: list[ScheduleAssignment],
                     kept: list[ScheduleAssignment],
                     outcome: RepairOutcome,
                     snapshot: ScheduleSnapshot) -> tuple[list[ScheduleAssignment], RepairOutcome]:
    """방어 로직 — 증분 검증을 통과했는데도 전수 검사에서 위반이 나오면
    새 배정을 하나씩 되돌려 위반 0 을 강제한다. (정상 경로에서는 호출되지 않는다)
    """
    survivors = list(new_assignments)
    while survivors and check_hard_constraints(kept + survivors, snapshot.interviewers):
        dropped = survivors.pop()
        for change in outcome.changes:
            if change.applicant_id == dropped.applicant_id and change.action == "rebook":
                change.action = "defer"
                change.reason = "ROLLED_BACK_UNSAFE"
                change.to_slot = None
                if not change.team_match:
                    outcome.cross_team_count = max(0, outcome.cross_team_count - 1)
                change.team_match = True
    return kept + survivors, outcome


def _assert_untouched(kept: list[ScheduleAssignment],
                      result: list[ScheduleAssignment]) -> None:
    result_map = {a.assignment_id: a for a in result}
    for a in kept:
        after = result_map.get(a.assignment_id)
        if after is None or (after.day, after.hour, after.interviewer_id) != (
                a.day, a.hour, a.interviewer_id):
            raise AssertionError(
                f"재편성이 대상 외 배정을 변경했다: {a.assignment_id} ({a.lock_level})")
