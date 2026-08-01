"""하드 제약 검증 단위 테스트"""
from __future__ import annotations

from app.domain.schemas import InterviewerIn
from app.infrastructure.contracts import DAYS, HOURS
from app.services.constraint_checker import (
    check_hard_constraints,
    off_band,
    soft_penalty,
    validate,
)
from app.services.rule_evaluator import rule_compliance

H = HOURS


def IV(iv_id="IV101", team="AI솔루션팀", max_daily=6, priority=2, availability=None):
    return InterviewerIn(
        interviewer_id=iv_id,
        name=iv_id,
        team=team,
        max_daily=max_daily,
        priority=priority,
        availability=availability if availability is not None else {d: list(HOURS) for d in DAYS},
    )


def A(applicant_id, iv_id="IV101", team="AI솔루션팀", day="1일차", hour=H[0], degree="학사"):
    return {
        "applicant_id": applicant_id,
        "interviewer_id": iv_id,
        "team": team,
        "day": day,
        "hour": hour,
        "degree": degree,
    }


def _codes(violations):
    return {v["code"] for v in violations}


def test_clean_schedule_has_no_violation():
    assignments = [A("1", hour=H[0]), A("2", hour=H[1])]
    assert check_hard_constraints(assignments, [IV()]) == []


def test_detects_team_conflict():
    assignments = [A("1", iv_id="IV101"), A("2", iv_id="IV102")]
    violations = check_hard_constraints(assignments, [IV("IV101"), IV("IV102")])
    assert "TEAM_CONFLICT" in _codes(violations)


def test_detects_interviewer_conflict():
    """같은 면접관이 같은 시간에 두 건 (팀이 달라 규칙2로는 안 잡힘)"""
    assignments = [A("1", team="A팀"), A("2", team="B팀")]
    violations = check_hard_constraints(assignments, [IV()])
    assert "INTERVIEWER_CONFLICT" in _codes(violations)


def test_detects_daily_limit_exceeded():
    assignments = [
        A(str(i), team=f"T{i}", hour=hour) for i, hour in enumerate(HOURS)
    ]  # 하루 한 칸씩 전부
    violations = check_hard_constraints(assignments, [IV(max_daily=3)])
    codes = _codes(violations)
    assert "DAILY_LIMIT_EXCEEDED" in codes
    limit = next(v for v in violations if v["code"] == "DAILY_LIMIT_EXCEEDED")
    assert limit["count"] == len(HOURS) and limit["limit"] == 3


def test_detects_availability_violation():
    iv = IV(availability={"1일차": [H[3], H[4]]})
    violations = check_hard_constraints([A("1", hour=H[0])], [iv])
    assert "AVAILABILITY_VIOLATION" in _codes(violations)


def test_detects_unavailable_day():
    iv = IV(availability={"2일차": list(HOURS)})
    violations = check_hard_constraints([A("1", day="1일차")], [iv])
    assert "AVAILABILITY_VIOLATION" in _codes(violations)


def test_detects_unknown_slot():
    violations = check_hard_constraints([A("1", day="8일차", hour="20시")], [IV()])
    assert "UNKNOWN_SLOT" in _codes(violations)


def test_unknown_interviewer_is_not_availability_checked():
    """면접관 정보를 못 받은 경우 가용성 검증은 건너뛴다 (오탐 방지)"""
    violations = check_hard_constraints([A("1", iv_id="GHOST")], [])
    assert violations == []


def test_soft_penalty_grows_with_leader_load():
    leader = IV("IV101", priority=1)
    assignments = [A(str(i), hour=h) for i, h in enumerate(HOURS[:3])]
    report = rule_compliance(assignments)
    penalty = soft_penalty(report, assignments, [leader])
    assert penalty > 0


def test_soft_penalty_zero_on_perfect_plan(applicants, interviewers):
    from app.services import algorithm_v5

    plan = algorithm_v5.run(applicants, interviewers)
    report = rule_compliance(plan.assignments, interviewers, applicants)
    assert soft_penalty(report, plan.assignments, interviewers) == 0.0


def test_validate_bundles_hard_and_soft():
    result = validate([A("1"), A("2", iv_id="IV102")], [IV("IV101"), IV("IV102")])
    assert "hard_violations" in result and "soft_penalty" in result
    assert result["hard_violations"]


# ---------------------------------------- 담당자 일정 무시하고 배치하기

def test_ignoring_availability_is_not_a_violation():
    """인사가 일부러 고른 어긋남은 위반으로 세지 않는다.

    자리를 채우려고 '담당자 일정 무시하고 배치하기'를 켠 것이므로, 이걸
    규칙 위반으로 세면 인사가 스스로 고른 결과를 놓고 경고를 받게 된다.
    """
    iv = IV(availability={"1일차": [H[3], H[4]]})
    assert check_hard_constraints([A("1", hour=H[0])], [iv], ignore_availability=True) == []


def test_ignoring_availability_still_catches_real_conflicts():
    """가용성만 봐 주는 것이지, 겹치기까지 봐 주지는 않는다."""
    assignments = [A("1", iv_id="IV101"), A("2", iv_id="IV102")]
    violations = check_hard_constraints(
        assignments, [IV("IV101"), IV("IV102")], ignore_availability=True
    )
    assert "TEAM_CONFLICT" in _codes(violations)


def test_off_band_lists_who_to_call():
    """어긋난 자리는 위반이 아니어도 명단으로 남는다 — 개별 조율용."""
    iv = IV(availability={"1일차": [H[3], H[4]]})
    rows = off_band([A("1", hour=H[0]), A("2", hour=H[3])], [iv])
    assert [r["applicant_id"] for r in rows] == ["1"]
    assert rows[0]["interviewer_id"] == "IV101" and rows[0]["hour"] == H[0]


def test_off_band_flags_a_day_with_no_slot_at_all():
    """그날 칸이 하나도 없는 담당자에게 자리가 갔으면 명단에 남는다."""
    rows = off_band([A("1", day="1일차")], [IV(availability={"2일차": list(HOURS)})])
    assert len(rows) == 1 and "그날 맡을 수 있는 칸이 없음" in rows[0]["message"]


def test_off_band_is_empty_when_everyone_fits():
    assert off_band([A("1", hour=H[0]), A("2", hour=H[1])], [IV()]) == []


def test_validate_reports_off_band_when_ignoring():
    iv = IV(availability={"1일차": [H[3]]})
    result = validate([A("1", hour=H[0])], [iv], ignore_availability=True)
    assert result["hard_violations"] == []
    assert len(result["off_band"]) == 1
