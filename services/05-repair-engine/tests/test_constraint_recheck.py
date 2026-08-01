"""하드 제약 재검증 · 소프트 페널티 테스트"""
from datetime import datetime

from shared.contracts.constants import HOURS

from app.domain.schedule import (ApplicantInfo, InterviewerInfo,
                                 ScheduleAssignment, ScheduleSnapshot)
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

#: 두 날 모두 AI솔루션팀이 그날 크게 잡고, 미래혁신팀은 한 명씩만 보는 시간표.
#: AI솔루션팀이 4타임과 7타임을 차지하고 있어, 기본 조건에서는 7타임이 · 1시간
#: 면접에서는 4타임이 오후의 첫 칸이 되면서 걸리는 자리가 달라진다.
_BIG_TEAM_ROWS = [
    _a("1", "P1", "IV001", "1일차", HOURS[3], "AI솔루션팀"),
    _a("2", "P2", "IV001", "2일차", HOURS[3], "AI솔루션팀"),
    _a("3", "P3", "IV002", "1일차", HOURS[6], "AI솔루션팀"),
    _a("4", "P4", "IV002", "1일차", HOURS[1], "AI솔루션팀"),
    _a("5", "P5", "IV002", "1일차", HOURS[2], "AI솔루션팀"),
    _a("6", "P6", "IV003", "1일차", HOURS[7], "미래혁신팀"),
    _a("7", "P7", "IV002", "2일차", HOURS[4], "AI솔루션팀"),
    _a("8", "P8", "IV003", "2일차", HOURS[5], "미래혁신팀"),
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


def _grad_rows(day: str, total: int, grads: int):
    """그 날에 total 건 — 그중 grads 건이 대학원. 팀 · 담당자는 안 겹치게 돌린다."""
    rows, aps = [], []
    for i in range(total):
        aid = f"{day}-{i}"
        rows.append(_a(aid, aid, "IV002", day, HOURS[i % len(HOURS)], "AI솔루션팀"))
        aps.append(ApplicantInfo(applicant_id=aid, name=aid, team_1st="AI솔루션팀",
                                 degree_type="대학원" if i < grads else "학사"))
    return rows, aps


def test_grad_balance_is_measured_against_the_round_not_a_fixed_30_percent():
    """대학원생이 반 할뿐인 회차를 고르게 나눴으면 벌점이 없어야 한다.

    3할로 못 박으면 어느 날도 허용 범위(1~5할)에 못 들어 온 날이 벌점이다.
    줄일 길이 없는 벌점을 줄이겠다고 재편성이 멀쩡한 자리를 흔들게 되고,
    04 규칙1은 만점을 주는데 05만 깎는 어긋남도 생긴다.
    """
    rows, aps = [], []
    for day in ("1일차", "2일차", "3일차"):
        r, a = _grad_rows(day, 8, 0 if day == "2일차" else 1)   # 2/24 ≒ 0.08
        rows += r
        aps += a

    assert soft_penalty_breakdown(rows, IVS, aps)["RULE1_GRAD_BALANCE"] == 0


def test_grad_balance_still_catches_a_day_piled_with_grads():
    """명단 비율을 목표로 삼아도 한쪽으로 몰린 날은 잡아야 한다."""
    rows, aps = [], []
    for day, grads in (("1일차", 0), ("2일차", 0), ("3일차", 8)):
        r, a = _grad_rows(day, 8, grads)
        rows += r
        aps += a

    assert soft_penalty_breakdown(rows, IVS, aps)["RULE1_GRAD_BALANCE"] > 0


def test_first_slot_penalty_follows_the_interview_timing():
    """'첫 타임' 이 몇 번째 칸인지는 면접 진행 조건이 정한다.

    30분 면접 · 5분 휴식이면 7타임이 12:30 — 오후의 첫 칸이다. 1시간 면접 ·
    10분 휴식이면 같은 12:30이 4타임이다. 재편성이 이 조건을 안 보면 원래
    시간표가 지킨 칸과 다른 칸을 지키려 들어, 고칠수록 규칙4가 무너진다.
    """
    # 기본 조건 — 큰 조가 차지한 첫 칸은 1일차 7타임 하나뿐이다
    assert soft_penalty_breakdown(_BIG_TEAM_ROWS, IVS, [])["RULE4_FIRST_SLOT"] == W_FIRST_SLOT
    # 1시간 면접 — 4타임이 오후 첫 칸이 되어 이틀치가 걸린다
    assert soft_penalty_breakdown(
        _BIG_TEAM_ROWS, IVS, [], HOUR_LONG)["RULE4_FIRST_SLOT"] == 2 * W_FIRST_SLOT


def test_team_that_cannot_dodge_the_first_slot_is_not_charged():
    """하루를 통째로 쓰는 조는 첫 칸을 피할 길이 없다 — 벌점을 물리지 않는다.

    작은 조가 그 칸을 함께 잡고 있으면 '수요 적은 조부터' 는 지켜진 것이다.
    04 규칙4도 이 시간표를 만점으로 본다. 두 잣대가 갈리면 04가 만점을 준
    자리를 05가 벌점으로 깎아, 재편성이 멀쩡한 자리를 흔들게 된다.
    """
    rows = [_a(f"B{i}", f"PB{i}", "IV002", "1일차", HOURS[i], "AI솔루션팀")
            for i in range(6)]
    rows += [_a(f"S{i}", f"PS{i}", "IV003", "1일차", HOURS[i], "미래혁신팀")
             for i in range(2)]
    assert soft_penalty_breakdown(rows, IVS, [])["RULE4_FIRST_SLOT"] == 0

    # 작은 조가 첫 칸을 비우고 뒤로 물러나면 그때 걸린다
    moved = [r for r in rows if r.assignment_id != "S0"]
    moved.append(_a("S0", "PS0", "IV003", "1일차", HOURS[2], "미래혁신팀"))
    assert soft_penalty_breakdown(moved, IVS, [])["RULE4_FIRST_SLOT"] == W_FIRST_SLOT


def test_team_size_is_counted_per_day_not_per_round():
    """조가 크다 · 작다는 **그날** 기준이다. 회차 전체 건수로 재면 안 된다.

    AI솔루션팀은 회차 전체로는 6명을 보지만 2일차에는 한 명뿐이다. 그날 첫
    칸에 앉아도 지각으로 밀릴 줄이 없으니 흠이 아니다. 회차 전체로 재면
    '큰 조가 첫 칸을 차지했다' 며 애먼 자리를 벌한다.
    """
    rows = [_a(f"A{i}", f"PA{i}", "IV002", "1일차", HOURS[i], "AI솔루션팀")
            for i in range(1, 6)]
    rows.append(_a("A0", "PA0", "IV003", "1일차", HOURS[1], "미래혁신팀"))
    # 2일차 — AI솔루션팀 1명(첫 칸), 미래혁신팀 2명
    rows.append(_a("B0", "PB0", "IV001", "2일차", HOURS[0], "AI솔루션팀"))
    rows += [_a(f"B{i}", f"PB{i}", "IV003", "2일차", HOURS[i], "미래혁신팀")
             for i in (1, 2)]

    assert soft_penalty_breakdown(rows, IVS, [])["RULE4_FIRST_SLOT"] == 0


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
