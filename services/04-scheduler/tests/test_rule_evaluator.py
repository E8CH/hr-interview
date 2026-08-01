"""4대 규칙 준수율 계산 단위 테스트"""
from __future__ import annotations

from app.infrastructure.contracts import HOURS as H
from app.services.rule_evaluator import (
    rule1_grad_balance,
    rule2_team_conflict,
    rule3_vertical_group,
    rule4_first_slot,
    rule_compliance,
)


def A(team="AI솔루션팀", degree="학사", day="1일차", hour=H[0], **kw):
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
    """해당 날에 total건(그중 grads건이 대학원) 생성 — 시간대는 순환"""
    hours = list(H)
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
# 규칙 1 — 날별 대학원 비율
# --------------------------------------------------------------------------
def test_rule1_grad_detects_a_day_at_45_percent():
    """1일차 대학원 45% 상황에서 편차를 감지한다"""
    assignments = _day_of("1일차", 20, 9)  # 9/20 = 45%
    assignments += _day_of("2일차", 20, 6)  # 30%
    assignments += _day_of("5일차", 20, 0)  # 0% — 허용 범위 이탈

    score, detail = rule1_grad_balance(assignments, target=0.30, tolerance=0.20)

    assert detail["ratios"]["1일차"] == 0.45
    assert detail["ratios"]["5일차"] == 0.0
    # 기본 허용범위 10~50%: 1일차(45%)·2일차(30%)는 통과, 5일차(0%)만 이탈
    assert detail["outside"] == ["5일차"]
    assert score == round(100 * 2 / 3, 1)

    # 허용 오차를 10%p로 조이면 1일차 45%도 편차로 잡힌다
    tight_score, tight_detail = rule1_grad_balance(assignments, target=0.30, tolerance=0.10)
    assert "1일차" in tight_detail["outside"]
    assert tight_score < score


def test_rule1_perfect_balance():
    assignments = _day_of("1일차", 10, 3) + _day_of("2일차", 10, 3)
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
        A(applicant_id="2"),  # 같은 팀·같은 날·같은 시간 → 중복
        A(applicant_id="3", hour=H[1]),
    ]
    score, detail = rule2_team_conflict(assignments)

    assert detail["conflict_count"] == 1
    assert detail["conflicts"][0]["count"] == 2
    assert score < 100.0


def test_rule2_clean_is_100():
    assignments = [A(hour=H[0]), A(hour=H[1]), A(team="미래혁신팀", hour=H[0])]
    score, detail = rule2_team_conflict(assignments)
    assert score == 100.0
    assert detail["conflicts"] == []


def test_rule2_empty_is_100():
    assert rule2_team_conflict([])[0] == 100.0


# --------------------------------------------------------------------------
# 규칙 3 — 세로 연속 배치
# --------------------------------------------------------------------------
def test_rule3_contiguous_block_passes():
    assignments = [A(hour=h) for h in (H[0], H[1], H[2])]
    score, detail = rule3_vertical_group(assignments)
    assert score == 100.0
    assert detail["broken"] == []


def test_rule3_gap_breaks_group():
    assignments = [A(hour=H[0]), A(hour=H[1]), A(hour=H[4])]
    score, detail = rule3_vertical_group(assignments)
    assert score == 0.0
    assert detail["broken"][0]["team"] == "AI솔루션팀"


def test_rule3_mixed_groups():
    good = [A(team="A팀", hour=h) for h in (H[0], H[1])]
    bad = [A(team="B팀", hour=H[0]), A(team="B팀", hour=H[5])]
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
        A(team="A팀", hour=H[0]),
        A(team="B팀", hour=H[0]),
        A(team="A팀", hour=H[1]),
    ]
    score, detail = rule4_first_slot(assignments)
    assert score == 0.0
    assert detail["violations"][0]["first_count"] == 2


def test_rule4_ramp_up_passes():
    assignments = [
        A(team="A팀", hour=H[0]),
        A(team="A팀", hour=H[1]),
        A(team="B팀", hour=H[1]),
    ]
    score, _ = rule4_first_slot(assignments)
    assert score == 100.0


def test_rule4_single_hour_block_not_evaluated():
    assignments = [A(hour=H[0])]
    score, detail = rule4_first_slot(assignments)
    assert score == 100.0
    assert detail["evaluated"] == 0


def test_rule4_afternoon_first_slot_follows_the_interview_timing():
    """오후 첫 타임이 몇 번째 칸인지는 면접 진행 조건이 정한다.

    1시간 면접 · 10분 휴식이면 4타임이 12:30 — 점심 뒤 처음 오는 자리라 지각
    위험을 따로 봐야 한다. 거기에 큰 조가 앉으면 규칙4 위반이다. 그런데 기본
    조건(30분 · 5분)에서 4타임은 10:45 로 오전 한복판이라, 똑같은 배정이 위반이
    아니다. 칸 번호로 굳혀 두면 둘 중 한쪽을 반드시 틀리게 잰다.
    """
    rows = [
        A(team="A팀", hour=H[2]),
        A(team="B팀", hour=H[3]),
        A(team="C팀", hour=H[3]),
        A(team="A팀", hour=H[4]),
    ]

    score, detail = rule4_first_slot(rows, {"start": "09:00", "minutes": 60, "rest": 10})
    assert detail["first_slots"] == [H[0], H[3]]
    assert score == 0.0
    assert [v["hour"] for v in detail["violations"]] == [H[3]]

    score, detail = rule4_first_slot(rows)          # 기본 조건 — 30분 · 5분
    assert detail["first_slots"] == [H[0], H[6]]
    assert score == 100.0
    assert detail["violations"] == []


def test_rule_compliance_passes_the_timing_down_to_rule4():
    """시간표를 만들 때와 나중에 다시 잴 때 같은 조건을 넘겨야 점수가 같다."""
    rows = [A(team="A팀", hour=H[3]), A(team="B팀", hour=H[3]), A(team="A팀", hour=H[4])]
    long_day = {"start": "09:00", "minutes": 60, "rest": 10}

    assert rule_compliance(rows, timing=long_day).scores["rule4_first_slot"] == 0.0
    assert rule_compliance(rows).scores["rule4_first_slot"] == 100.0


# --------------------------------------------------------------------------
# 통합
# --------------------------------------------------------------------------
def test_overall_is_mean_of_four_rules():
    """명세 예시(60/100/100/100 → 90.0)와 동일한 산식"""
    assignments = _day_of("1일차", 20, 9) + _day_of("5일차", 20, 0)
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
