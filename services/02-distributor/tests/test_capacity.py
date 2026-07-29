"""정원 처리 테스트 — 정원 초과 시 차순위 팀으로 (OVERFLOW_REASSIGN)."""
import pytest

from app.domain.profile import TeamProfile
from app.services.distributor_engine import distribute
from tests.test_scorer import make_applicant


def two_team_profiles(cap_a: int, cap_b: int) -> list[TeamProfile]:
    return [
        TeamProfile(
            team_name="AI솔루션팀",
            primary_job=["직무다"],
            secondary_job=[],
            preferred_majors=["미르지능학과"],
            org_allowed=["제1기술원"],
            grad_ratio_target=0.5,
            target_headcount=cap_a,
        ),
        TeamProfile(
            team_name="미래혁신팀",
            primary_job=[],
            secondary_job=["직무다"],
            preferred_majors=[],
            org_allowed=["제1기술원"],
            grad_ratio_target=0.5,
            target_headcount=cap_b,
        ),
    ]


def test_overflow_goes_to_next_team():
    """모두 AI솔루션팀이 1순위지만 정원이 2명 → 나머지는 미래혁신팀."""
    applicants = [make_applicant(applicant_id=f"A{i}", degree_type="과정1") for i in range(5)]
    result = distribute(applicants, two_team_profiles(2, 3), allow_duplicate=False)

    assert result.team_counts == {"AI솔루션팀": 2, "미래혁신팀": 3}
    assert result.unassigned == []
    overflowed = [a for a in result.assignments if a.team_name == "미래혁신팀"]
    assert len(overflowed) == 3
    assert all("OVERFLOW_REASSIGN" in a.tags for a in overflowed)


def test_capacity_never_exceeded():
    applicants = [make_applicant(applicant_id=f"B{i}") for i in range(20)]
    profiles = two_team_profiles(3, 4)
    result = distribute(applicants, profiles, allow_duplicate=False)

    for profile in profiles:
        assert result.team_counts[profile.team_name] == profile.target_headcount
    assert len(result.unassigned) == 20 - 7


def test_ineligible_candidates_are_left_unassigned():
    """조직 조건을 만족하는 후보가 없으면 어떤 팀에도 배정되지 않는다."""
    applicants = [make_applicant(applicant_id=f"C{i}", team_1st="제3연구소") for i in range(3)]
    result = distribute(applicants, two_team_profiles(2, 2), allow_duplicate=False)
    assert result.team_counts == {"AI솔루션팀": 0, "미래혁신팀": 0}
    assert len(result.unassigned) == 3


def test_repair_pass_fills_quota_when_greedy_blocks():
    """탐욕 배정이 막아버린 슬롯을 증대경로 보정이 채운다.

    - 후보 X: 제2사업부 → 미래혁신팀만 가능
    - 후보 Y, Z: 제1기술원 → 두 팀 모두 가능하지만 미래혁신팀 점수가 더 높음
    정원(AI 1 / 미래 1)에서 Y·Z가 미래혁신팀을 선점하면 X가 남으므로, 보정이 동작해야 한다.
    """
    profiles = [
        TeamProfile(
            team_name="AI솔루션팀",
            primary_job=[],
            secondary_job=["직무다"],
            preferred_majors=[],
            org_allowed=["제1기술원"],
            grad_ratio_target=0.5,
            target_headcount=1,
        ),
        TeamProfile(
            team_name="미래혁신팀",
            primary_job=["직무다"],
            secondary_job=[],
            preferred_majors=["미르지능학과"],
            org_allowed=["제1기술원", "제2사업부"],
            grad_ratio_target=0.5,
            target_headcount=1,
        ),
    ]
    applicants = [
        make_applicant(applicant_id="X", team_1st="제2사업부"),
        make_applicant(applicant_id="Y", team_1st="제1기술원"),
    ]
    result = distribute(applicants, profiles, allow_duplicate=False)

    assert result.unassigned == []
    assert result.team_counts == {"AI솔루션팀": 1, "미래혁신팀": 1}
    placed = {a.applicant_id: a.team_name for a in result.assignments}
    assert placed["X"] == "미래혁신팀"  # X는 미래혁신팀만 가능
    assert placed["Y"] == "AI솔루션팀"


def test_every_assignment_has_at_least_two_tags():
    applicants = [make_applicant(applicant_id=f"D{i}", major_final=None) for i in range(4)]
    result = distribute(applicants, two_team_profiles(2, 2), allow_duplicate=False)
    assert result.assignments
    for assignment in result.assignments:
        assert len(assignment.tags) >= 2, assignment


def test_grad_balance_swap_tags():
    """학위비율 목표에서 벗어난 배정은 스왑으로 보정되고 GRAD_BALANCE 태그가 붙는다."""
    profiles = [
        TeamProfile(
            team_name="AI솔루션팀",
            primary_job=["직무다"],
            secondary_job=[],
            preferred_majors=[],
            org_allowed=["제1기술원"],
            grad_ratio_target=0.0,
            target_headcount=2,
        ),
        TeamProfile(
            team_name="미래혁신팀",
            primary_job=["직무다"],
            secondary_job=[],
            preferred_majors=[],
            org_allowed=["제1기술원"],
            grad_ratio_target=1.0,
            target_headcount=2,
        ),
    ]
    # ID 순서상 대학원생(A*)이 먼저 AI솔루션팀(목표 대학원비율 0)을 채우게 만든 뒤,
    # 스왑 패스가 이를 되돌리는지 확인한다.
    applicants = [
        make_applicant(applicant_id="A1", degree_type="과정2"),
        make_applicant(applicant_id="A2", degree_type="과정2"),
        make_applicant(applicant_id="C1", degree_type="과정1"),
        make_applicant(applicant_id="C2", degree_type="과정1"),
    ]
    result = distribute(applicants, profiles, allow_duplicate=False)

    placed = {a.applicant_id: a.team_name for a in result.assignments if not a.is_duplicate}
    assert placed["A1"] == placed["A2"] == "미래혁신팀"
    assert placed["C1"] == placed["C2"] == "AI솔루션팀"
    assert any("GRAD_BALANCE" in a.tags for a in result.assignments)


def test_empty_input():
    result = distribute([], two_team_profiles(2, 2))
    assert result.assignments == []
    assert result.total_applicants == 0
    assert result.duplicate_count == 0


def test_prefilter_applied():
    applicants = [
        make_applicant(applicant_id="P1"),
        make_applicant(applicant_id="F1", doc_result="결과F"),
        make_applicant(applicant_id="N1", rnd_type="구분N"),
    ]
    result = distribute(applicants, two_team_profiles(5, 5), allow_duplicate=False)
    assert result.filtered_count == 1
    assert {a.applicant_id for a in result.assignments} == {"P1"}


@pytest.mark.parametrize("cap", [0, 1, 3])
def test_zero_and_small_capacity(cap):
    applicants = [make_applicant(applicant_id=f"Z{i}") for i in range(3)]
    result = distribute(applicants, two_team_profiles(cap, 0), allow_duplicate=False)
    assert result.team_counts["AI솔루션팀"] == min(cap, 3)
