"""배치 알고리즘 3종 — 명세 테스트 요구사항"""
from __future__ import annotations

import time

import pytest

from app.domain.schemas import ApplicantIn, GenerateConstraints
from app.services import algorithm_v1, algorithm_v4, algorithm_v5, registry
from app.services.constraint_checker import check_hard_constraints
from app.services.rule_evaluator import rule_compliance

ALL_ALGORITHMS = ["v1", "v4", "v5"]


def _coverage(plan, applicants) -> float:
    return 100.0 * len(plan.assignments) / len(applicants)


def test_algorithm_v1_full_coverage_no_hard_violation(applicants, interviewers):
    """88명 100% 배정, 하드 위반 0"""
    plan = algorithm_v1.run(applicants, interviewers)

    assert len(applicants) == 88
    assert len(plan.assignments) == 88
    assert plan.unassigned == []
    assert _coverage(plan, applicants) == 100.0
    assert check_hard_constraints(plan.assignments, interviewers) == []


def test_algorithm_v1_shows_grad_imbalance_tradeoff(applicants, interviewers):
    """v1은 커버리지를 얻는 대신 규칙1(요일 분산)이 무너진다 — v4/v5의 존재 이유"""
    plan = algorithm_v1.run(applicants, interviewers)
    report = rule_compliance(plan.assignments, interviewers, applicants)

    assert report.scores["rule1_grad_balance"] < 85.0
    assert report.details["rule1_grad_balance"]["outside"]


def test_algorithm_v4_rule_compliance_and_vertical_group(applicants, interviewers):
    """4대 규칙 90% 이상, 세로 연속 100%"""
    plan = algorithm_v4.run(applicants, interviewers)
    report = rule_compliance(plan.assignments, interviewers, applicants)

    assert report.overall >= 90.0
    assert report.scores["rule3_vertical_group"] == 100.0
    assert report.details["rule3_vertical_group"]["broken"] == []
    assert check_hard_constraints(plan.assignments, interviewers) == []


def test_algorithm_v4_trades_coverage(applicants, interviewers):
    """팀당 요일 2개 제한 → 커버리지가 100%에 못 미친다 (명세상 68% 수준)"""
    plan = algorithm_v4.run(applicants, interviewers)
    coverage = _coverage(plan, applicants)

    assert 60.0 <= coverage < 90.0
    assert len(plan.unassigned) == len(applicants) - len(plan.assignments)
    assert all(len(days) == 2 for days in plan.notes["team_days"].values())


def test_algorithm_v5_recovers_coverage_and_keeps_rules(applicants, interviewers):
    """v5: 커버리지 90% 이상 AND 규칙 준수 85% 이상 동시 달성"""
    plan = algorithm_v5.run(applicants, interviewers)
    report = rule_compliance(plan.assignments, interviewers, applicants)

    assert _coverage(plan, applicants) >= 90.0
    assert report.overall >= 85.0
    assert all(len(days) == 3 for days in plan.notes["team_days"].values())
    assert plan.notes["stage3_fallback"] is True


def test_v5_beats_v4_on_coverage_and_v1_on_rules(applicants, interviewers):
    v1 = algorithm_v1.run(applicants, interviewers)
    v4 = algorithm_v4.run(applicants, interviewers)
    v5 = algorithm_v5.run(applicants, interviewers)

    assert len(v5.assignments) > len(v4.assignments)
    r1 = rule_compliance(v1.assignments, interviewers, applicants).overall
    r5 = rule_compliance(v5.assignments, interviewers, applicants).overall
    assert r5 > r1


@pytest.mark.parametrize("algorithm", ALL_ALGORITHMS)
def test_hard_violation_zero(algorithm, applicants, interviewers):
    """어떤 알고리즘도 하드 위반을 발생시키지 않는다"""
    plan = registry.run(algorithm, applicants, interviewers)
    violations = check_hard_constraints(plan.assignments, interviewers)

    assert violations == [], f"{algorithm}: {violations[:3]}"
    assert rule_compliance(plan.assignments).scores["rule2_team_conflict"] == 100.0


@pytest.mark.parametrize("algorithm", ALL_ALGORITHMS)
def test_algorithms_are_fast(algorithm, applicants, interviewers):
    started = time.perf_counter()
    registry.run(algorithm, applicants, interviewers)
    assert (time.perf_counter() - started) < 3.0


@pytest.mark.parametrize("algorithm", ALL_ALGORITHMS)
def test_no_duplicate_applicant(algorithm, applicants, interviewers):
    plan = registry.run(algorithm, applicants, interviewers)
    ids = [a.applicant_id for a in plan.assignments]
    assert len(ids) == len(set(ids))


def test_stage3_fallback_absorbs_overflow(interviewers):
    """한 팀이 3요일 수용량(18)을 넘기면 Stage 3가 남은 요일로 흡수한다"""
    team = "AI솔루션팀"
    overflow = [
        ApplicantIn(
            applicant_id=f"OV{i:03d}",
            name=f"초과{i}",
            team=team,
            degree="대학원" if i % 3 == 0 else "학사",
            priority_score=4.0 - i * 0.01,
        )
        for i in range(22)
    ]

    v4_plan = algorithm_v4.run(overflow, interviewers)
    v5_plan = algorithm_v5.run(overflow, interviewers)

    assert len(v4_plan.assignments) < len(v5_plan.assignments)
    assert len(v5_plan.assignments) == 22  # 22 > 18 → fallback이 나머지 4명을 흡수
    assert v5_plan.unassigned == []
    assert check_hard_constraints(v5_plan.assignments, interviewers) == []
    assert any("HR_MANUAL" in a.reason_tags for a in v5_plan.assignments)


def test_capacity_limit_leaves_unassigned(interviewers):
    """팀 수용량(5요일 × 6타임 = 30)을 넘으면 남는 지원자가 생긴다"""
    team = "전극기술팀"
    crowd = [
        ApplicantIn(applicant_id=f"C{i:03d}", name=f"과밀{i}", team=team, degree="학사")
        for i in range(35)
    ]
    plan = algorithm_v5.run(crowd, interviewers)

    assert len(plan.assignments) == 30
    assert len(plan.unassigned) == 5
    assert check_hard_constraints(plan.assignments, interviewers) == []


def test_constraints_are_honored(applicants, interviewers):
    constraints = GenerateConstraints(grad_ratio_target=0.5, grad_ratio_tolerance=0.05)
    plan = algorithm_v5.run(applicants, interviewers, constraints)
    assert len(plan.assignments) > 0


def test_registry_rejects_unknown_algorithm(applicants, interviewers):
    with pytest.raises(registry.UnknownAlgorithmError):
        registry.run("v99", applicants, interviewers)


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("v4_hierarchical", "v4"),
        ("V5", "v5"),
        ("interviewer_first", "v1"),
        ("integrated", "v5"),
    ],
)
def test_algorithm_aliases(alias, expected):
    assert registry.normalize_algorithm(alias) == expected
