"""4대 규칙 준수율 계산 단위 테스트"""
from __future__ import annotations

from app.services.rule_evaluator import (
    rule1_grad_balance,
    rule2_team_conflict,
    rule3_vertical_group,
    rule4_first_slot,
    rule_compliance,
)


def A(team="AI솔루션팀", degree="학사", day="월", hour="09시", **kw):
    base = {
        "team": team,
        "degree": degree,
        "day": day,
        "hour": hour,
        "applicant_id": kw.pop("applicant_id", "X"),
        "interviewer_id": kw.pop("interviewer_id", "IV101"),
    }
    base.update(kw)
    return base


def _day_of(day, total, grads, team="AI솔루션팀"):
    """해당 요일에 total건(그중 grads건이 대학원) 생성 — 시간대는 순환"""
    hours = ["09시", "10시", "11시", "14시", "15시", "16시"]
    rows = []
    for i in range(total):
        rows.append(
            A(
                team=f"{team}{i // len(hours)}",
                degree="대학원" if i < grads else "학사",
                day=day,
                hour=hours[i % len(hours)],
                applicant_id=f"{day}-{i}",
            )
        )
    return rows


# --------------------------------------------------------------------------
# 규칙 1 — 요일별 대학원 비율
# --------------------------------------------------------------------------
def test_rule1_grad_detects_monday_45_percent():
    """월요일 대학원 45% 상황에서 편차를 감지한다"""
    assignments = _day_of("월", 20, 9)  # 9/20 = 45%
    assignments += _day_of("화", 20, 6)  # 30%
    assignments += _day_of("금", 20, 0)  # 0% — 허용 범위 이탈

    score, detail = rule1_grad_balance(assignments, target=0.30, tolerance=0.20)

    assert detail["ratios"]["월"] == 0.45
    assert detail["ratios"]["금"] == 0.0
    # 기본 허용범위 10~50%: 월(45%)·화(30%)는 통과, 금(0%)만 이탈
    assert detail["outside"] == ["금"]
    assert score == round(100 * 2 / 3, 1)

    # 허용 오차를 10%p로 조이면 월요일 45%도 편차로 잡힌다
    tight_score, tight_detail = rule1_grad_balance(assignments, target=0.30, tolerance=0.10)
    assert "월" in tight_detail["outside"]
    assert tight_score < score


def test_rule1_perfect_balance():
    assignments = _day_of("월", 10, 3) + _day_of("화", 10, 3)
    score, detail = rule1_grad_balance(assignments, 0.30, 0.20)
    assert score == 100.0
    assert detail["outside"] == []


def test_rule1_empty_is_neutral():
    score, detail = rule1_grad_balance([], 0.30, 0.20)
    assert score == 100.0
    assert detail["ratios"] == {}


# --------------------------------------------------------------------------
# 규칙 2 — 팀 동시간 중복 (HARD)
# --------------------------------------------------------------------------
def test_rule2_detects_team_conflict():
    assignments = [
        A(applicant_id="1"),
        A(applicant_id="2"),  # 같은 팀·같은 요일·같은 시간 → 중복
        A(applicant_id="3", hour="10시"),
    ]
    score, detail = rule2_team_conflict(assignments)

    assert detail["conflict_count"] == 1
    assert detail["conflicts"][0]["count"] == 2
    assert score < 100.0


def test_rule2_clean_is_100():
    assignments = [A(hour="09시"), A(hour="10시"), A(team="미래혁신팀", hour="09시")]
    score, detail = rule2_team_conflict(assignments)
    assert score == 100.0
    assert detail["conflicts"] == []


def test_rule2_empty_is_100():
    assert rule2_team_conflict([])[0] == 100.0


# --------------------------------------------------------------------------
# 규칙 3 — 세로 연속 배치
# --------------------------------------------------------------------------
def test_rule3_contiguous_block_passes():
    assignments = [A(hour=h) for h in ("09시", "10시", "11시")]
    score, detail = rule3_vertical_group(assignments)
    assert score == 100.0
    assert detail["broken"] == []


def test_rule3_gap_breaks_group():
    assignments = [A(hour="09시"), A(hour="10시"), A(hour="15시")]
    score, detail = rule3_vertical_group(assignments)
    assert score == 0.0
    assert detail["broken"][0]["team"] == "AI솔루션팀"


def test_rule3_mixed_groups():
    good = [A(team="A팀", hour=h) for h in ("09시", "10시")]
    bad = [A(team="B팀", hour="09시"), A(team="B팀", hour="16시")]
    score, detail = rule3_vertical_group(good + bad)
    assert score == 50.0
    assert detail["groups"] == 2


def test_rule3_empty_is_100():
    assert rule3_vertical_group([])[0] == 100.0


# --------------------------------------------------------------------------
# 규칙 4 — 첫 타임 소규모
# --------------------------------------------------------------------------
def test_rule4_first_slot_heavier_than_rest_fails():
    assignments = [
        A(team="A팀", hour="09시"),
        A(team="B팀", hour="09시"),
        A(team="A팀", hour="10시"),
    ]
    score, detail = rule4_first_slot(assignments)
    assert score == 0.0
    assert detail["violations"][0]["first_count"] == 2


def test_rule4_ramp_up_passes():
    assignments = [
        A(team="A팀", hour="09시"),
        A(team="A팀", hour="10시"),
        A(team="B팀", hour="10시"),
    ]
    score, _ = rule4_first_slot(assignments)
    assert score == 100.0


def test_rule4_single_hour_block_not_evaluated():
    assignments = [A(hour="09시")]
    score, detail = rule4_first_slot(assignments)
    assert score == 100.0
    assert detail["evaluated"] == 0


# --------------------------------------------------------------------------
# 통합
# --------------------------------------------------------------------------
def test_overall_is_mean_of_four_rules():
    """명세 예시(60/100/100/100 → 90.0)와 동일한 산식"""
    assignments = _day_of("월", 20, 9) + _day_of("금", 20, 0)
    report = rule_compliance(assignments)
    expected = sum(
        report.scores[k]
        for k in (
            "rule1_grad_balance",
            "rule2_team_conflict",
            "rule3_vertical_group",
            "rule4_first_slot",
        )
    ) / 4
    assert report.overall == round(expected + 1e-9, 1)


def test_report_shapes():
    report = rule_compliance([A()])
    flat = report.flat()
    verbose = report.verbose()

    assert set(flat) == {
        "rule1_grad_balance",
        "rule2_team_conflict",
        "rule3_vertical_group",
        "rule4_first_slot",
        "overall",
    }
    assert "score" in verbose["rule1_grad_balance"]
    assert "detail" in verbose["rule1_grad_balance"]


def test_rule_evaluator_accepts_objects(applicants, interviewers):
    from app.services import algorithm_v5

    plan = algorithm_v5.run(applicants, interviewers)
    report = rule_compliance(plan.assignments, interviewers, applicants)
    assert 0.0 <= report.overall <= 100.0
