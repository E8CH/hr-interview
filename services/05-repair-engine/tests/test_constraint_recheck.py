"""하드 제약 재검증 · 소프트 페널티 테스트"""
from datetime import datetime

from shared.contracts.constants import HOURS

from app.domain.schedule import (InterviewerInfo, ScheduleAssignment,
                                 ScheduleSnapshot)
from app.services.constraint_recheck import (W_FIRST_SLOT, ConstraintIndex,
                                             check_hard_constraints,
                                             compute_soft_penalty,
                                             soft_penalty_breakdown)
from app.services.safe_repair import repair_safely

IVS = [
    InterviewerInfo(interviewer_id="IV001", name="A", team="AI솔루션팀", max_daily=2),
    InterviewerInfo(interviewer_id="IV002", name="B", team="AI솔루션팀", max_daily=6),
    InterviewerInfo(interviewer_id="IV003", name="C", team="미래혁신팀", max_daily=6),
]


def _a(aid, ap, iv, day, hour, team, lock="DRAFT"):
    return ScheduleAssignment(assignment_id=aid, applicant_id=ap, interviewer_id=iv,
                              day=day, hour=hour, team=team, lock_level=lock,
                              created_at=datetime(2026, 7, 1))


HOUR_LONG = {"start": "09:00", "minutes": 60, "rest": 10}

#: 대형 조(AI솔루션팀 5건)가 4타임 · 7타임을 차지한 시간표. 기본 조건에서는
#: 7타임이, 1시간 면접에서는 4타임이 오후의 첫 칸이라 걸리는 자리가 달라진다.
_BIG_TEAM_ROWS = [
    _a("1", "P1", "IV001", "1일차", HOURS[3], "AI솔루션팀"),
    _a("2", "P2", "IV001", "2일차", HOURS[3], "AI솔루션팀"),
    _a("3", "P3", "IV002", "1일차", HOURS[6], "AI솔루션팀"),
    _a("4", "P4", "IV002", "1일차", HOURS[1], "AI솔루션팀"),
    _a("5", "P5", "IV002", "1일차", HOURS[2], "AI솔루션팀"),
    _a("6", "P6", "IV003", "1일차", HOURS[7], "미래혁신팀"),
]


def test_no_violation_on_clean_schedule():
    rows = [_a("1", "P1", "IV001", "1일차", "09시", "AI솔루션팀"),
            _a("2", "P2", "IV003", "1일차", "09시", "미래혁신팀")]
    assert check_hard_constraints(rows, IVS) == []


def test_rule2_same_team_same_slot_detected():
    """규칙2(HARD): 같은 팀 동시간 중복"""
    rows = [_a("1", "P1", "IV001", "1일차", "09시", "AI솔루션팀"),
            _a("2", "P2", "IV002", "1일차", "09시", "AI솔루션팀")]
    violations = check_hard_constraints(rows, IVS)
    assert [v.rule for v in violations] == ["RULE2_TEAM_CONFLICT"]
    assert violations[0].severity == "HARD"


def test_interviewer_double_booking_detected():
    rows = [_a("1", "P1", "IV003", "2일차", "10시", "미래혁신팀"),
            _a("2", "P2", "IV003", "2일차", "10시", "미래혁신팀")]
    rules = {v.rule for v in check_hard_constraints(rows, IVS)}
    assert "H2_INTERVIEWER_DOUBLE_BOOK" in rules


def test_applicant_double_booking_detected():
    rows = [_a("1", "P1", "IV001", "3일차", "11시", "AI솔루션팀"),
            _a("2", "P1", "IV003", "3일차", "11시", "미래혁신팀")]
    rules = {v.rule for v in check_hard_constraints(rows, IVS)}
    assert "H3_APPLICANT_DOUBLE_BOOK" in rules


def test_max_daily_exceeded_detected():
    rows = [_a("1", "P1", "IV001", "4일차", "09시", "AI솔루션팀"),
            _a("2", "P2", "IV001", "4일차", "10시", "AI솔루션팀"),
            _a("3", "P3", "IV001", "4일차", "11시", "AI솔루션팀")]
    rules = {v.rule for v in check_hard_constraints(rows, IVS)}
    assert "H4_MAX_DAILY_EXCEEDED" in rules   # max_daily=2 인데 3건


def test_index_blocks_team_conflict():
    idx = ConstraintIndex.build([_a("1", "P1", "IV001", "1일차", "09시", "AI솔루션팀")], IVS)
    assert not idx.is_safe("P9", "1일차", "09시", "IV002", "AI솔루션팀")
    assert "H1_TEAM_CONFLICT" in idx.blocking_reasons("P9", "1일차", "09시", "IV002", "AI솔루션팀")
    assert idx.is_safe("P9", "1일차", "10시", "IV002", "AI솔루션팀")


def test_index_blocks_max_daily():
    rows = [_a("1", "P1", "IV001", "1일차", "09시", "AI솔루션팀"),
            _a("2", "P2", "IV001", "1일차", "10시", "AI솔루션팀")]
    idx = ConstraintIndex.build(rows, IVS)
    assert "H4_MAX_DAILY_EXCEEDED" in idx.blocking_reasons(
        "P9", "1일차", "11시", "IV001", "AI솔루션팀")


def test_index_add_remove_symmetry():
    row = _a("1", "P1", "IV001", "1일차", "09시", "AI솔루션팀")
    idx = ConstraintIndex.build([row], IVS)
    assert not idx.is_safe("P9", "1일차", "09시", "IV002", "AI솔루션팀")
    idx.remove(row)
    assert idx.is_safe("P9", "1일차", "09시", "IV002", "AI솔루션팀")


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
    """하루를 대학원생으로만 채우면 규칙1 페널티가 올라간다"""
    ap_map = snapshot.applicant_map()
    for a in snapshot.assignments:
        if a.day == "1일차":
            ap_map[a.applicant_id].degree_type = "대학원"
    breakdown = soft_penalty_breakdown(snapshot.assignments, snapshot.interviewers,
                                       list(ap_map.values()))
    assert breakdown["RULE1_GRAD_BALANCE"] > 0


def test_first_slot_penalty_follows_the_interview_timing():
    """'첫 타임' 이 몇 번째 칸인지는 면접 진행 조건이 정한다.

    30분 면접 · 5분 휴식이면 7타임이 12:30 — 오후의 첫 칸이다. 1시간 면접 ·
    10분 휴식이면 같은 12:30이 4타임이다. 재편성이 이 조건을 안 보면 원래
    시간표가 지킨 칸과 다른 칸을 지키려 들어, 고칠수록 규칙4가 무너진다.
    """
    # 기본 조건 — 대형 조가 차지한 첫 칸은 7타임 하나뿐이다
    assert soft_penalty_breakdown(_BIG_TEAM_ROWS, IVS, [])["RULE4_FIRST_SLOT"] == W_FIRST_SLOT
    # 1시간 면접 — 4타임이 오후 첫 칸이 되어 이틀치가 걸린다
    assert soft_penalty_breakdown(
        _BIG_TEAM_ROWS, IVS, [], HOUR_LONG)["RULE4_FIRST_SLOT"] == 2 * W_FIRST_SLOT


def test_repair_reads_the_timing_off_the_snapshot():
    """스냅샷이 조건을 들고 다녀야 재편성이 04 와 같은 칸을 지킨다."""
    snapshot = ScheduleSnapshot(
        schedule_id="SCH-TIMING", round_id="R-TIMING",
        assignments=list(_BIG_TEAM_ROWS), interviewers=IVS, timing=HOUR_LONG)

    outcome = repair_safely(snapshot, [])       # 옮길 사람이 없어도 점수는 다시 잰다
    assert outcome.hard_violations == 0
    assert outcome.soft_penalty == compute_soft_penalty(
        _BIG_TEAM_ROWS, IVS, [], HOUR_LONG)
    # 조건을 안 넘기면 4타임을 오전 한복판으로 보므로 더 낮게 나온다
    assert outcome.soft_penalty > compute_soft_penalty(_BIG_TEAM_ROWS, IVS, [])


def test_empty_schedule_has_no_penalty():
    assert compute_soft_penalty([], IVS, []) == 0
    assert check_hard_constraints([], IVS) == []
