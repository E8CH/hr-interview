"""6개 축 스코어링 (명세 02 §Design 스코어링 알고리즘).

축: R&D · 조직 · 직무 · 전공 · 학위비율 · 특수태그
- 직무 / 조직 / 전공 / 특수태그(타겟랩·지도교수)는 후보 단위 점수로 계산한다.
- R&D는 배포 파이프라인의 사전 필터(`구분R`)로 반영된다.
- 학위비율은 후보 단독으로 판단할 수 없으므로 배정 단계(distributor_engine)의
  소프트 제약으로 처리하고 `GRAD_BALANCE` 태그를 부여한다.
"""
from __future__ import annotations

from contracts.types import Applicant

from app.domain.profile import TeamProfile

#: 조직 조건 불충족 — 해당 팀 배정 불가
INELIGIBLE_SCORE = -100.0

MAIN_ORG = "제1기술원"

DOC_PASS = "결과P"
RND_TYPE = "구분R"

#: 학사 과정 코드 (그 외 과정2/과정3 = 대학원)
BACHELOR_CODE = "과정1"


def is_graduate(candidate: Applicant) -> bool:
    """최종학력이 대학원(과정2/과정3)인지."""
    return (candidate.degree_type or "").strip() != BACHELOR_CODE


def passes_prefilter(candidate: Applicant) -> bool:
    """배포 대상 필터: 1차서류 결과P AND R&D 구분R."""
    return candidate.doc_result == DOC_PASS and candidate.rnd_type == RND_TYPE


def score_candidate(candidate: Applicant, profile: TeamProfile) -> tuple[float, list[str]]:
    """후보 × 팀 프로필 → (점수, 사유 태그).

    조직 조건을 만족하지 못하면 `(INELIGIBLE_SCORE, [])`를 반환한다.
    """
    score: float = 0.0
    tags: list[str] = []

    # --- 직무 축 ---
    if candidate.job_1st in profile.primary_job:
        score += 5
        tags.append("PRIMARY_JOB")
    elif candidate.job_1st in profile.secondary_job:
        score += 2
        tags.append("SECONDARY_JOB")

    # --- 조직 축 (불충족 시 배정 불가) ---
    if candidate.team_1st == MAIN_ORG:
        score += 1
        tags.append("ORG_MAIN")
    elif candidate.team_1st in profile.org_allowed:
        score += 0.5
        tags.append("ORG_ALT_QUOTA")
    else:
        return INELIGIBLE_SCORE, []

    # --- 전공 축 ---
    majors = {candidate.major_final, candidate.major_bachelor} - {None}
    if majors & set(profile.preferred_majors):
        score += 3
        tags.append("PREFERRED_MAJOR")

    # --- 특수태그 축 ---
    if candidate.target_lab == "Y" and "타겟랩" in profile.special_tags:
        score += 10
        tags.append("TARGET_LAB")
    if candidate.advisor and "지도교수" in profile.special_tags:
        score += 5
        tags.append("ADVISOR_ROUTE")

    return score, tags


def score_against_all(
    candidate: Applicant, profiles: list[TeamProfile]
) -> list[tuple[str, float, list[str]]]:
    """후보를 전체 팀에 대해 채점하고 점수 내림차순으로 반환 (배정 가능 팀만)."""
    results = []
    for profile in profiles:
        score, tags = score_candidate(candidate, profile)
        if score <= INELIGIBLE_SCORE:
            continue
        results.append((profile.team_name, score, tags))
    results.sort(key=lambda row: (-row[1], row[0]))
    return results
