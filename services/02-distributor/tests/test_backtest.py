"""백테스트 — **실제 취합파일 467명**과 **실제 배포 결과(희망지원자_*.xlsx)** 대조.

데이터 출처: `docs/취합파일.xlsx`, `docs/희망지원자_{팀}.xlsx`
  → `data/취합파일.xlsx`, `tests/fixtures/희망지원자_{팀}.xlsx` 로 복사

명세 §완료 판정 체크리스트 대응:
- 마스터 파일 입력 → 5개 팀 배포안 3초 이내 생성
- 팀별 정원 오차 0
- 모든 assignment에 최소 2개 태그
"""
import time

import pytest

from app.services.distributor_engine import distribute, filter_applicants
from app.services.scorer import is_graduate

EXPECTED_TEAM_COUNTS = {
    "AI솔루션팀": 16,
    "로봇응용기술팀": 19,
    "미래혁신팀": 17,
    "배터리기술팀": 16,
    "전극기술팀": 20,
}
EXPECTED_TOTAL = sum(EXPECTED_TEAM_COUNTS.values())  # 88
MASTER_SIZE = 467

#: 개별 지원자 재현율 회귀 감시 하한.
#: 명세는 개인 재현율이 5.7% 수준이라 HR 검수 결합이 필수라고 명시한다.
#: 현재 엔진 실측은 팀까지 일치 10건(11.4%) — 아래 값은 "떨어지면 회귀"의 바닥선이다.
MIN_TEAM_LEVEL_MATCHES = 8
MIN_SELECTION_MATCHES = 15


@pytest.fixture
def result(master_applicants, profiles):
    return distribute(master_applicants, profiles)


def test_master_file_shape(master_applicants):
    """실제 취합파일은 467명이고 전원이 결과P · 구분R 이다."""
    assert len(master_applicants) == MASTER_SIZE
    assert len({a.applicant_id for a in master_applicants}) == MASTER_SIZE
    # 467명 → 88명을 만드는 것은 사전 필터가 아니라 팀 정원이다
    assert len(filter_applicants(master_applicants)) == MASTER_SIZE


def test_truth_file_shape(team_truth):
    """정답지: 팀별 정원 · 총 88건 · 유니크 83명 · 중복 배포 5건."""
    assert {t: len(v) for t, v in team_truth.items()} == EXPECTED_TEAM_COUNTS
    total = sum(len(v) for v in team_truth.values())
    unique = len(set().union(*team_truth.values()))
    assert total == EXPECTED_TOTAL == 88
    assert unique == 83
    assert total - unique == 5  # 명세가 말한 중복 배포 5건


def test_team_headcounts_exactly_match_targets(result, profiles):
    """팀별 정원 오차 0 — 5개 팀 100% 재현 (실데이터)."""
    assert result.team_counts == EXPECTED_TEAM_COUNTS
    assert result.total_applicants == EXPECTED_TOTAL
    for profile in profiles:
        assert result.team_counts[profile.team_name] == profile.target_headcount
    # 정원(88) 밖의 지원자는 미배정으로 남는다
    assert len(result.unassigned) == MASTER_SIZE - EXPECTED_TOTAL


def test_generates_within_three_seconds(master_applicants, profiles):
    started = time.perf_counter()
    distribute(master_applicants, profiles)
    elapsed = time.perf_counter() - started
    assert elapsed < 3.0, f"배포안 생성 {elapsed:.2f}s — 3초 초과"


def test_every_assignment_has_min_two_tags(result):
    assert len(result.assignments) >= EXPECTED_TOTAL
    for assignment in result.assignments:
        assert len(assignment.tags) >= 2, assignment
        assert len(set(assignment.tags)) == len(assignment.tags)


def test_selection_recall_against_truth(result, team_truth):
    """정답지 88건 중 우리 엔진이 '선발'한 사람이 얼마나 겹치는가 (팀 무관)."""
    truth_ids = set().union(*team_truth.values())
    predicted_ids = {a.applicant_id for a in result.assignments}
    matched = truth_ids & predicted_ids
    assert len(matched) >= MIN_SELECTION_MATCHES, (
        f"선발 재현 {len(matched)}/{len(truth_ids)} — 하한 {MIN_SELECTION_MATCHES} 미만"
    )


def test_team_level_recall_against_truth(result, team_truth):
    """팀까지 일치하는 건수. 명세가 인정한 대로 낮으며, HR 검수 결합이 전제다."""
    predicted: dict[str, set[str]] = {}
    for assignment in result.assignments:
        predicted.setdefault(assignment.team_name, set()).add(assignment.applicant_id)

    matches = sum(len(team_truth[t] & predicted.get(t, set())) for t in team_truth)
    assert matches >= MIN_TEAM_LEVEL_MATCHES, (
        f"팀 일치 {matches}/{EXPECTED_TOTAL} — 하한 {MIN_TEAM_LEVEL_MATCHES} 미만 (회귀)"
    )


def test_deterministic_reruns(master_applicants, profiles):
    """같은 입력 → 같은 배포 결과 (담당자 재량 없이 재현 가능)."""
    def snapshot(res):
        return sorted(
            (a.applicant_id, a.team_name, a.score, tuple(a.tags), a.is_duplicate)
            for a in res.assignments
        )

    assert snapshot(distribute(master_applicants, profiles)) == snapshot(
        distribute(master_applicants, profiles)
    )


def test_no_applicant_assigned_twice_to_same_team(result):
    seen = set()
    for assignment in result.assignments:
        key = (assignment.applicant_id, assignment.team_name)
        assert key not in seen
        seen.add(key)


def test_primary_assignment_is_unique_per_applicant(result):
    primaries = [a.applicant_id for a in result.assignments if not a.is_duplicate]
    assert len(primaries) == len(set(primaries)) == EXPECTED_TOTAL


def test_duplicates_reference_primary_team(result):
    primary_team = {
        a.applicant_id: a.team_name for a in result.assignments if not a.is_duplicate
    }
    duplicates = [a for a in result.assignments if a.is_duplicate]
    assert result.duplicate_count == len(duplicates)
    for dup in duplicates:
        assert dup.primary_team == primary_team[dup.applicant_id]
        assert dup.team_name != dup.primary_team
        assert "DUPLICATE_REVIEW" in dup.tags


def test_org_constraint_never_violated(master_applicants, profiles, result):
    """제1기술원이 아닌 지원자는 해당 팀 org_allowed에 포함되어야 한다."""
    by_id = {a.applicant_id: a for a in master_applicants}
    by_team = {p.team_name: p for p in profiles}
    for assignment in result.assignments:
        applicant = by_id[assignment.applicant_id]
        profile = by_team[assignment.team_name]
        assert applicant.team_1st == "제1기술원" or applicant.team_1st in profile.org_allowed


def test_grad_ratio_within_rule1_tolerance(master_applicants, profiles, result):
    """규칙 1 (SOFT): 대학원 비율 목표 ±20%p 이내."""
    by_id = {a.applicant_id: a for a in master_applicants}
    per_team: dict[str, list[str]] = {}
    for assignment in result.assignments:
        if assignment.is_duplicate:
            continue
        per_team.setdefault(assignment.team_name, []).append(assignment.applicant_id)

    for profile in profiles:
        ids = per_team[profile.team_name]
        ratio = sum(1 for i in ids if is_graduate(by_id[i])) / len(ids)
        assert abs(ratio - profile.grad_ratio_target) <= 0.20 + 1e-9, (
            f"{profile.team_name} 대학원비율 {ratio:.2f} (목표 {profile.grad_ratio_target})"
        )


def test_seed_profiles_total_matches_target(profiles):
    assert sum(p.target_headcount for p in profiles) == EXPECTED_TOTAL
    assert len(profiles) == 5
