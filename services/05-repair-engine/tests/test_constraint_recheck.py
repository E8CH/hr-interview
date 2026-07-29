"""하드 제약 재검증 · 소프트 페널티 테스트"""
from datetime import datetime

from app.domain.schedule import InterviewerInfo, ScheduleAssignment
from app.services.constraint_recheck import (ConstraintIndex,
                                             check_hard_constraints,
                                             compute_soft_penalty,
                                             soft_penalty_breakdown)

IVS = [
    InterviewerInfo(interviewer_id="IV001", name="A", team="AI솔루션팀", max_daily=2),
    InterviewerInfo(interviewer_id="IV002", name="B", team="AI솔루션팀", max_daily=6),
    InterviewerInfo(interviewer_id="IV003", name="C", team="미래혁신팀", max_daily=6),
]


def _a(aid, ap, iv, day, hour, team, lock="DRAFT"):
    return ScheduleAssignment(assignment_id=aid, applicant_id=ap, interviewer_id=iv,
                              day=day, hour=hour, team=team, lock_level=lock,
                              created_at=datetime(2026, 7, 1))


def test_no_violation_on_clean_schedule():
    rows = [_a("1", "P1", "IV001", "월", "09시", "AI솔루션팀"),
            _a("2", "P2", "IV003", "월", "09시", "미래혁신팀")]
    assert check_hard_constraints(rows, IVS) == []


def test_rule2_same_team_same_slot_detected():
    """규칙2(HARD): 같은 팀 동시간 중복"""
    rows = [_a("1", "P1", "IV001", "월", "09시", "AI솔루션팀"),
            _a("2", "P2", "IV002", "월", "09시", "AI솔루션팀")]
    violations = check_hard_constraints(rows, IVS)
    assert [v.rule for v in violations] == ["RULE2_TEAM_CONFLICT"]
    assert violations[0].severity == "HARD"


def test_interviewer_double_booking_detected():
    rows = [_a("1", "P1", "IV003", "화", "10시", "미래혁신팀"),
            _a("2", "P2", "IV003", "화", "10시", "미래혁신팀")]
    rules = {v.rule for v in check_hard_constraints(rows, IVS)}
    assert "H2_INTERVIEWER_DOUBLE_BOOK" in rules


def test_applicant_double_booking_detected():
    rows = [_a("1", "P1", "IV001", "수", "11시", "AI솔루션팀"),
            _a("2", "P1", "IV003", "수", "11시", "미래혁신팀")]
    rules = {v.rule for v in check_hard_constraints(rows, IVS)}
    assert "H3_APPLICANT_DOUBLE_BOOK" in rules


def test_max_daily_exceeded_detected():
    rows = [_a("1", "P1", "IV001", "목", "09시", "AI솔루션팀"),
            _a("2", "P2", "IV001", "목", "10시", "AI솔루션팀"),
            _a("3", "P3", "IV001", "목", "11시", "AI솔루션팀")]
    rules = {v.rule for v in check_hard_constraints(rows, IVS)}
    assert "H4_MAX_DAILY_EXCEEDED" in rules   # max_daily=2 인데 3건


def test_index_blocks_team_conflict():
    idx = ConstraintIndex.build([_a("1", "P1", "IV001", "월", "09시", "AI솔루션팀")], IVS)
    assert not idx.is_safe("P9", "월", "09시", "IV002", "AI솔루션팀")
    assert "H1_TEAM_CONFLICT" in idx.blocking_reasons("P9", "월", "09시", "IV002", "AI솔루션팀")
    assert idx.is_safe("P9", "월", "10시", "IV002", "AI솔루션팀")


def test_index_blocks_max_daily():
    rows = [_a("1", "P1", "IV001", "월", "09시", "AI솔루션팀"),
            _a("2", "P2", "IV001", "월", "10시", "AI솔루션팀")]
    idx = ConstraintIndex.build(rows, IVS)
    assert "H4_MAX_DAILY_EXCEEDED" in idx.blocking_reasons(
        "P9", "월", "11시", "IV001", "AI솔루션팀")


def test_index_add_remove_symmetry():
    row = _a("1", "P1", "IV001", "월", "09시", "AI솔루션팀")
    idx = ConstraintIndex.build([row], IVS)
    assert not idx.is_safe("P9", "월", "09시", "IV002", "AI솔루션팀")
    idx.remove(row)
    assert idx.is_safe("P9", "월", "09시", "IV002", "AI솔루션팀")


def test_soft_penalty_is_non_negative(snapshot):
    penalty = compute_soft_penalty(snapshot.assignments, snapshot.interviewers,
                                   snapshot.applicants)
    assert penalty >= 0
    breakdown = soft_penalty_breakdown(snapshot.assignments, snapshot.interviewers,
                                       snapshot.applicants)
    assert set(breakdown) == {"RULE1_GRAD_BALANCE", "RULE3_VERTICAL_GROUP",
                              "RULE4_FIRST_SLOT"}
    assert sum(breakdown.values()) == penalty


def test_grad_balance_penalty_triggers_on_skew(snapshot):
    """한 요일을 대학원생으로만 채우면 규칙1 페널티가 올라간다"""
    ap_map = snapshot.applicant_map()
    for a in snapshot.assignments:
        if a.day == "월":
            ap_map[a.applicant_id].degree_type = "대학원"
    breakdown = soft_penalty_breakdown(snapshot.assignments, snapshot.interviewers,
                                       list(ap_map.values()))
    assert breakdown["RULE1_GRAD_BALANCE"] > 0


def test_empty_schedule_has_no_penalty():
    assert compute_soft_penalty([], IVS, []) == 0
    assert check_hard_constraints([], IVS) == []
