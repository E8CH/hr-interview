"""중복 배포 테스트 — 1위 대비 2위 점수 비율이 임계값 이상이면 두 팀에 배포."""
import pytest

from app.domain.profile import TeamProfile
from app.services.distributor_engine import distribute
from tests.test_scorer import make_applicant


def _profile(name: str, **overrides) -> TeamProfile:
    base = dict(
        team_name=name,
        primary_job=["직무다"],
        secondary_job=[],
        preferred_majors=[],
        org_allowed=["제1기술원"],
        grad_ratio_target=0.5,
        target_headcount=1,
    )
    base.update(overrides)
    return TeamProfile(**base)


def close_profiles() -> list[TeamProfile]:
    """두 팀 모두 동일 조건 → 점수 6 : 6 (비율 1.0)."""
    return [_profile("AI솔루션팀"), _profile("배터리기술팀")]


def distant_profiles() -> list[TeamProfile]:
    """1위 6점(주력직무) vs 2위 3점(보조직무) → 비율 0.5."""
    return [
        _profile("AI솔루션팀"),
        _profile("배터리기술팀", primary_job=[], secondary_job=["직무다"]),
    ]


def candidate(applicant_id: str = "DUP1"):
    return make_applicant(applicant_id=applicant_id, major_final=None, major_bachelor=None)


def test_duplicate_created_above_threshold():
    result = distribute([candidate()], close_profiles(), duplicate_score_threshold=0.8)

    assert result.duplicate_count == 1
    primary = [a for a in result.assignments if not a.is_duplicate]
    duplicate = [a for a in result.assignments if a.is_duplicate]
    assert len(primary) == 1 and len(duplicate) == 1
    assert duplicate[0].primary_team == primary[0].team_name
    assert duplicate[0].team_name != primary[0].team_name
    assert "DUPLICATE_REVIEW" in duplicate[0].tags
    # 중복 배포는 정원을 소비하지 않는다
    assert result.team_counts == {"AI솔루션팀": 1, "배터리기술팀": 0}
    assert result.total_applicants == 1


def test_no_duplicate_below_threshold():
    result = distribute([candidate("DUP2")], distant_profiles(), duplicate_score_threshold=0.8)
    assert result.duplicate_count == 0
    assert all(not a.is_duplicate for a in result.assignments)


def test_allow_duplicate_false_disables():
    result = distribute([candidate("DUP3")], close_profiles(), allow_duplicate=False)
    assert result.duplicate_count == 0


def test_single_eligible_team_never_duplicates():
    """배정 가능한 팀이 하나뿐이면 중복 배포 대상이 아니다."""
    profiles = [_profile("AI솔루션팀"), _profile("배터리기술팀", org_allowed=["제2사업부"])]
    solo = make_applicant(applicant_id="DUP4", team_1st="제2사업부", major_final=None)
    # 제2사업부 후보는 AI솔루션팀 조직 조건 미충족 → 배터리기술팀만 가능
    result = distribute([solo], profiles, duplicate_score_threshold=0.0)
    assert result.duplicate_count == 0
    assert result.team_counts == {"AI솔루션팀": 0, "배터리기술팀": 1}


@pytest.mark.parametrize(
    "threshold,expected",
    [(0.0, 1), (0.4, 1), (0.5, 1), (0.6, 0), (0.9, 0)],
)
def test_threshold_boundary(threshold, expected):
    """distant_profiles의 점수 비율은 0.5 — 임계값 경계에서 켜지고 꺼진다."""
    result = distribute(
        [candidate("DUP5")], distant_profiles(), duplicate_score_threshold=threshold
    )
    assert result.duplicate_count == expected


def test_all_rows_have_two_tags():
    result = distribute([candidate("DUP6")], close_profiles(), duplicate_score_threshold=0.8)
    for assignment in result.assignments:
        assert len(assignment.tags) >= 2
