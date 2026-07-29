"""스코어링 단위 테스트 — 같은 후보라도 팀 프로필에 따라 다른 점수."""
import pytest
from contracts.types import Applicant

from app.domain.profile import TeamProfile, seed_profiles
from app.services.scorer import (
    INELIGIBLE_SCORE,
    is_graduate,
    passes_prefilter,
    score_against_all,
    score_candidate,
)


def make_applicant(**overrides) -> Applicant:
    base = dict(
        applicant_id="3339449",
        name="김민준",
        team_1st="제1기술원",
        job_1st="직무다",
        rnd_type="구분R",
        degree_type="과정2",
        major_final="미르지능학과",
        major_bachelor=None,
        gpa_final=4.1,
        target_lab="N",
        advisor=None,
        prev_applications=0,
        doc_result="결과P",
    )
    base.update(overrides)
    return Applicant(**base)


def profile_by_name(name: str) -> TeamProfile:
    return next(p for p in seed_profiles() if p.team_name == name)


def test_same_candidate_scores_differently_per_team():
    """동일 후보 × 서로 다른 팀 프로필 → 점수가 달라야 한다."""
    candidate = make_applicant()
    ai_score, ai_tags = score_candidate(candidate, profile_by_name("AI솔루션팀"))
    battery_score, battery_tags = score_candidate(candidate, profile_by_name("배터리기술팀"))

    # AI솔루션팀: 직무다=주력(5) + 제1기술원(1) + 미르지능학과=선호(3) = 9
    assert ai_score == pytest.approx(9.0)
    assert set(ai_tags) == {"PRIMARY_JOB", "ORG_MAIN", "PREFERRED_MAJOR"}

    # 배터리기술팀: 직무다=보조(2) + 제1기술원(1) = 3
    assert battery_score == pytest.approx(3.0)
    assert set(battery_tags) == {"SECONDARY_JOB", "ORG_MAIN"}
    assert ai_score != battery_score


def test_org_not_allowed_is_ineligible():
    """조직 조건 불충족 시 -100 · 태그 없음."""
    candidate = make_applicant(team_1st="제2사업부")
    score, tags = score_candidate(candidate, profile_by_name("AI솔루션팀"))
    assert score == INELIGIBLE_SCORE
    assert tags == []


def test_org_alt_quota_tag():
    candidate = make_applicant(team_1st="제2사업부", job_1st="직무나", major_final=None)
    score, tags = score_candidate(candidate, profile_by_name("전극기술팀"))
    assert score == pytest.approx(5.5)  # 주력직무 5 + 제2사업부 쿼터 0.5
    assert set(tags) == {"PRIMARY_JOB", "ORG_ALT_QUOTA"}


def test_unknown_org_is_ineligible_everywhere():
    candidate = make_applicant(team_1st="제3연구소")
    assert all(
        score_candidate(candidate, profile)[0] == INELIGIBLE_SCORE
        for profile in seed_profiles()
    )


def test_target_lab_and_advisor_bonus():
    """특수태그 축: 타겟랩 +10, 지도교수 +5 (해당 special_tags를 가진 팀만)."""
    candidate = make_applicant(job_1st="직무나", target_lab="Y", advisor="박서연교수")
    robot_score, robot_tags = score_candidate(candidate, profile_by_name("로봇응용기술팀"))
    electrode_score, electrode_tags = score_candidate(candidate, profile_by_name("전극기술팀"))

    assert "TARGET_LAB" in robot_tags
    assert "ADVISOR_ROUTE" in robot_tags
    assert "TARGET_LAB" not in electrode_tags
    assert robot_score == pytest.approx(electrode_score + 15)


def test_major_bachelor_also_matches():
    candidate = make_applicant(major_final="푸른화학과", major_bachelor="벼리재료학과")
    _score, tags = score_candidate(candidate, profile_by_name("AI솔루션팀"))
    assert "PREFERRED_MAJOR" in tags


def test_score_against_all_sorted_and_filters_ineligible():
    candidate = make_applicant(team_1st="제2사업부", job_1st="직무나")
    options = score_against_all(candidate, seed_profiles())
    names = [name for name, _s, _t in options]
    assert "AI솔루션팀" not in names  # org_allowed에 제2사업부 없음
    scores = [s for _n, s, _t in options]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize(
    "degree_type,expected",
    [("과정1", False), ("과정2", True), ("과정3", True)],
)
def test_is_graduate(degree_type, expected):
    assert is_graduate(make_applicant(degree_type=degree_type)) is expected


@pytest.mark.parametrize(
    "doc_result,rnd_type,expected",
    [
        ("결과P", "구분R", True),
        ("결과P", "구분N", False),
        ("결과F", "구분R", False),
        ("결과F", "구분N", False),
    ],
)
def test_prefilter(doc_result, rnd_type, expected):
    assert passes_prefilter(make_applicant(doc_result=doc_result, rnd_type=rnd_type)) is expected
