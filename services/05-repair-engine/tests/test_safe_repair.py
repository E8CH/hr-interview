"""명세 지정 테스트 — 안전 재편성 (v3.1)"""
import time

import pytest

from app.domain.repair_event import is_immovable
from app.services.constraint_recheck import check_hard_constraints
from app.services.plan_generator import generate_plans
from app.services.safe_repair import repair_safely


def _noshow_ratio(snapshot, ratio: float) -> list[str]:
    """전체 배정에서 `ratio` 비율만큼 결정적으로 노쇼자를 뽑는다."""
    step = max(1, int(1 / ratio))
    return [a.applicant_id for i, a in enumerate(snapshot.assignments) if i % step == 0]


def test_baseline_schedule_is_valid(snapshot):
    """재편성 전 mock 시간표 자체가 하드 위반 0 이어야 비교가 성립한다."""
    assert check_hard_constraints(snapshot.assignments, snapshot.interviewers) == []


def test_safe_repair_zero_violation(snapshot):
    """노쇼 20% 상황에서도 하드 위반 0"""
    noshow = _noshow_ratio(snapshot, 0.20)
    assert len(noshow) >= 13

    for allow_cross_team in (False, True):
        outcome = repair_safely(snapshot, noshow, allow_cross_team=allow_cross_team)
        assert outcome.hard_violations == 0, outcome.violation_details
        # 결과 배정을 독립적으로 다시 전수 검사
        assert check_hard_constraints(outcome.assignments, snapshot.interviewers) == []
        # 모든 대상자는 재예약 또는 이월 중 하나로 반드시 처리된다
        assert {c.applicant_id for c in outcome.changes} == set(noshow)


@pytest.mark.parametrize("ratio", [0.10, 0.20, 0.33, 0.50, 1.0])
def test_zero_violation_across_noshow_ratios(snapshot, ratio):
    noshow = _noshow_ratio(snapshot, ratio)
    outcome = repair_safely(snapshot, noshow, allow_cross_team=True)
    assert outcome.hard_violations == 0
    assert check_hard_constraints(outcome.assignments, snapshot.interviewers) == []


def test_locked_untouched(snapshot):
    """LOCKED 지원자는 재편성 시 자동 defer — 어떤 Plan 에서도 이동 없음"""
    locked_ids = [a.applicant_id for a in snapshot.assignments
                  if is_immovable(a.lock_level)]
    assert locked_ids, "mock 시간표에 LOCKED 배정이 있어야 한다"

    noshow = locked_ids[:5] + [a.applicant_id for a in snapshot.assignments
                               if a.lock_level == "DRAFT"][:5]

    for plan in generate_plans("EV-LOCK", snapshot, noshow):
        for change in plan.changes:
            if change.applicant_id in locked_ids:
                assert change.action == "defer", \
                    f"{plan.plan_type}: LOCKED {change.applicant_id} 가 이동됨"
                assert change.to_slot is None


def test_locked_non_target_never_moves(snapshot):
    """노쇼가 아닌 LOCKED 배정은 좌표가 그대로 유지된다"""
    noshow = [a.applicant_id for a in snapshot.assignments
              if a.lock_level == "DRAFT"][:10]
    before = {a.applicant_id: (a.day, a.hour, a.interviewer_id)
              for a in snapshot.assignments if a.lock_level == "LOCKED"}

    outcome = repair_safely(snapshot, noshow, allow_cross_team=True)
    after = {a.applicant_id: (a.day, a.hour, a.interviewer_id)
             for a in outcome.assignments}
    for applicant_id, slot in before.items():
        assert after[applicant_id] == slot


def test_lock_override_beats_snapshot_level(snapshot):
    """lock_map 으로 승격된 LOCKED 도 이동 불가"""
    target = next(a for a in snapshot.assignments if a.lock_level == "DRAFT")
    outcome = repair_safely(snapshot, [target.applicant_id],
                            {target.applicant_id: "LOCKED"}, allow_cross_team=True)
    assert outcome.changes[0].action == "defer"
    assert outcome.changes[0].reason == "LOCKED_IMMOVABLE"


def test_confirmed_move_incurs_penalty(snapshot):
    """CONFIRMED 는 이동 허용하되 페널티를 부여한다"""
    confirmed = next(a for a in snapshot.assignments if a.lock_level == "CONFIRMED")
    draft = next(a for a in snapshot.assignments
                 if a.lock_level == "DRAFT" and a.team == confirmed.team)

    moved_confirmed = repair_safely(snapshot, [confirmed.applicant_id])
    moved_draft = repair_safely(snapshot, [draft.applicant_id])
    assert moved_confirmed.changes[0].action == "rebook"
    assert moved_confirmed.soft_penalty > moved_draft.soft_penalty


def test_reopened_slots_reported(snapshot):
    """노쇼로 비게 된 슬롯은 반환 대상으로 보고된다"""
    noshow = [a.applicant_id for a in snapshot.assignments[:6]]
    outcome = repair_safely(snapshot, noshow)
    assert len(outcome.reopened_slots) == len(noshow)


def test_interviewer_cancel_excludes_their_slots(snapshot):
    """면접위원 취소 시 그 위원의 슬롯으로는 재예약되지 않는다"""
    target_iv = snapshot.assignments[0].interviewer_id
    affected = [a.applicant_id for a in snapshot.assignments
                if a.interviewer_id == target_iv]

    outcome = repair_safely(snapshot, affected, allow_cross_team=True,
                            excluded_interviewers={target_iv})
    assert outcome.hard_violations == 0
    for change in outcome.changes:
        if change.to_slot is not None:
            assert change.to_slot.interviewer_id != target_iv
    assert all(a.interviewer_id != target_iv for a in outcome.assignments)


def test_repair_completes_within_5s(snapshot):
    """완료 판정: 노쇼 13명 재편성 5초 이내 (Plan A/B/C 전체 생성 포함)"""
    noshow = [a.applicant_id for i, a in enumerate(snapshot.assignments)
              if i % 5 == 1][:13]
    started = time.perf_counter()
    plans = generate_plans("EV-PERF", snapshot, noshow)
    elapsed = time.perf_counter() - started
    assert len(plans) == 3
    assert elapsed < 5.0, f"재편성에 {elapsed:.2f}초 소요"
