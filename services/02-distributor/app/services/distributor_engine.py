"""배포 엔진 — 명세 02 §Design "배포 파이프라인".

1. 마스터 지원자 로드 (version_client)
2. `1차서류결과=결과P` AND `R&D=구분R` 필터
3. 각 지원자 × 5팀 스코어 계산
4. 스코어 상위 순 정원 채우기 → 정원 초과 시 차순위 팀 (`OVERFLOW_REASSIGN`)
   4-1. 탐욕 배정으로 못 채운 슬롯은 증대경로(augmenting path) 탐색으로 보정 → 정원 오차 0
   4-2. 학위비율(대학원 목표 비율) 편차를 줄이는 스왑 패스 → `GRAD_BALANCE`
5. 1위 대비 2위 점수 비율이 임계값 이상이면 중복 배포 (`DUPLICATE_REVIEW`)
6. 모든 배정에 최소 2개 태그 보장
"""
from __future__ import annotations

from dataclasses import dataclass, field

from contracts.types import Applicant

from app.domain.plan import Assignment
from app.domain.profile import TeamProfile
from app.services.scorer import (
    INELIGIBLE_SCORE,
    is_graduate,
    passes_prefilter,
    score_against_all,
)

#: 규칙 1 (SOFT) — 대학원 비율 목표 ±20%p
GRAD_TOLERANCE = 0.20

#: 학위비율 보정 스왑이 허용하는 총 점수 손실 상한
GRAD_SWAP_SCORE_BUDGET = 6.0

#: 스왑 패스 최대 반복 횟수 (무한루프 방지)
GRAD_SWAP_MAX_ITER = 200

MIN_TAGS = 2


@dataclass
class Candidate:
    """채점이 끝난 후보 1명."""

    applicant: Applicant
    #: [(team_name, score, tags), ...] 점수 내림차순
    options: list[tuple[str, float, list[str]]] = field(default_factory=list)

    @property
    def applicant_id(self) -> str:
        return self.applicant.applicant_id

    @property
    def is_grad(self) -> bool:
        return is_graduate(self.applicant)

    def score_for(self, team: str) -> float:
        for name, score, _tags in self.options:
            if name == team:
                return score
        return INELIGIBLE_SCORE

    def tags_for(self, team: str) -> list[str]:
        for name, _score, tags in self.options:
            if name == team:
                return list(tags)
        return []

    def eligible(self, team: str) -> bool:
        return self.score_for(team) > INELIGIBLE_SCORE

    @property
    def best_team(self) -> str | None:
        return self.options[0][0] if self.options else None


@dataclass
class DistributionResult:
    assignments: list[Assignment]
    team_counts: dict[str, int]
    total_applicants: int
    duplicate_count: int
    unassigned: list[str]
    filtered_count: int


def filter_applicants(applicants: list[Applicant]) -> list[Applicant]:
    """2단계: 결과P ∧ 구분R 필터."""
    return [a for a in applicants if passes_prefilter(a)]


def build_candidates(applicants: list[Applicant], profiles: list[TeamProfile]) -> list[Candidate]:
    """3단계: 지원자 × 팀 스코어 매트릭스."""
    return [Candidate(applicant=a, options=score_against_all(a, profiles)) for a in applicants]


def _greedy_fill(
    candidates: list[Candidate],
    capacity: dict[str, int],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """전체 (후보, 팀) 쌍을 점수 내림차순으로 훑으며 정원을 채운다."""
    pairs: list[tuple[float, str, str]] = []
    for cand in candidates:
        for team, score, _tags in cand.options:
            pairs.append((score, cand.applicant_id, team))
    # 점수 desc → 지원자 ID asc → 팀명 asc (결정적 정렬)
    pairs.sort(key=lambda row: (-row[0], row[1], row[2]))

    assigned: dict[str, str] = {}
    members: dict[str, list[str]] = {team: [] for team in capacity}

    for _score, applicant_id, team in pairs:
        if applicant_id in assigned:
            continue
        if len(members[team]) >= capacity[team]:
            continue
        assigned[applicant_id] = team
        members[team].append(applicant_id)
    return assigned, members


def _augment(
    applicant_id: str,
    by_id: dict[str, Candidate],
    assigned: dict[str, str],
    members: dict[str, list[str]],
    capacity: dict[str, int],
    visited: set[str],
) -> bool:
    """Kuhn 증대경로 — 미배정 후보를 위해 기존 배정을 재배치한다."""
    cand = by_id[applicant_id]
    for team, _score, _tags in cand.options:
        if team in visited:
            continue
        visited.add(team)
        if len(members[team]) < capacity[team]:
            assigned[applicant_id] = team
            members[team].append(applicant_id)
            return True
        for occupant in list(members[team]):
            members[team].remove(occupant)
            del assigned[occupant]
            if _augment(occupant, by_id, assigned, members, capacity, visited):
                assigned[applicant_id] = team
                members[team].append(applicant_id)
                return True
            members[team].append(occupant)
            assigned[occupant] = team
    return False


def _repair_capacity(
    candidates: list[Candidate],
    assigned: dict[str, str],
    members: dict[str, list[str]],
    capacity: dict[str, int],
) -> list[str]:
    """4-1단계: 탐욕 배정 후 남은 미배정자를 증대경로로 흡수. 남은 인원 ID 반환."""
    by_id = {c.applicant_id: c for c in candidates}
    leftovers = [c.applicant_id for c in candidates if c.applicant_id not in assigned]
    unassigned: list[str] = []
    for applicant_id in leftovers:
        if not _augment(applicant_id, by_id, assigned, members, capacity, set()):
            unassigned.append(applicant_id)
    return unassigned


def _grad_deviation(
    members: dict[str, list[str]],
    by_id: dict[str, Candidate],
    profiles_by_name: dict[str, TeamProfile],
) -> float:
    """전체 팀의 대학원 비율 목표 대비 편차 합."""
    total = 0.0
    for team, ids in members.items():
        if not ids:
            continue
        ratio = sum(1 for i in ids if by_id[i].is_grad) / len(ids)
        target = profiles_by_name[team].grad_ratio_target
        total += abs(ratio - target)
    return total


def _balance_grad_ratio(
    candidates: list[Candidate],
    assigned: dict[str, str],
    members: dict[str, list[str]],
    profiles_by_name: dict[str, TeamProfile],
) -> set[str]:
    """4-2단계: 정원을 유지한 채 학위비율 편차를 줄이는 1:1 스왑.

    스왑에 참여한 지원자 ID 집합을 반환 (→ GRAD_BALANCE 태그).
    """
    by_id = {c.applicant_id: c for c in candidates}
    swapped: set[str] = set()
    budget = GRAD_SWAP_SCORE_BUDGET

    for _ in range(GRAD_SWAP_MAX_ITER):
        base_dev = _grad_deviation(members, by_id, profiles_by_name)
        best: tuple[float, float, str, str] | None = None  # (gain, loss, a_id, b_id)

        teams = sorted(members)
        for i, team_a in enumerate(teams):
            for team_b in teams[i + 1 :]:
                for a_id in members[team_a]:
                    cand_a = by_id[a_id]
                    if not cand_a.eligible(team_b):
                        continue
                    for b_id in members[team_b]:
                        cand_b = by_id[b_id]
                        if cand_a.is_grad == cand_b.is_grad:
                            continue  # 비율이 바뀌지 않는 스왑
                        if not cand_b.eligible(team_a):
                            continue
                        loss = (
                            cand_a.score_for(team_a)
                            + cand_b.score_for(team_b)
                            - cand_a.score_for(team_b)
                            - cand_b.score_for(team_a)
                        )
                        if loss > budget:
                            continue
                        members[team_a].remove(a_id)
                        members[team_b].remove(b_id)
                        members[team_a].append(b_id)
                        members[team_b].append(a_id)
                        new_dev = _grad_deviation(members, by_id, profiles_by_name)
                        members[team_a].remove(b_id)
                        members[team_b].remove(a_id)
                        members[team_a].append(a_id)
                        members[team_b].append(b_id)

                        gain = base_dev - new_dev
                        if gain <= 1e-9:
                            continue
                        if best is None or (gain, -loss) > (best[0], -best[1]):
                            best = (gain, loss, a_id, b_id)

        if best is None:
            break

        _gain, loss, a_id, b_id = best
        team_a, team_b = assigned[a_id], assigned[b_id]
        members[team_a].remove(a_id)
        members[team_b].remove(b_id)
        members[team_a].append(b_id)
        members[team_b].append(a_id)
        assigned[a_id], assigned[b_id] = team_b, team_a
        swapped.update({a_id, b_id})
        budget -= max(loss, 0.0)
        if budget <= 0:
            break

    return swapped


def _ensure_min_tags(tags: list[str]) -> list[str]:
    """모든 배정에 최소 2개 태그 보장 (부족하면 HR 재량 태그로 보강)."""
    out = list(dict.fromkeys(tags))
    if len(out) < MIN_TAGS:
        out.append("HR_MANUAL")
    return out


def distribute(
    applicants: list[Applicant],
    profiles: list[TeamProfile],
    allow_duplicate: bool = True,
    duplicate_score_threshold: float = 0.8,
) -> DistributionResult:
    """배포 파이프라인 실행."""
    targets = filter_applicants(applicants)
    candidates = build_candidates(targets, profiles)
    profiles_by_name = {p.team_name: p for p in profiles}
    capacity = {p.team_name: p.target_headcount for p in profiles}

    assigned, members = _greedy_fill(candidates, capacity)
    unassigned = _repair_capacity(candidates, assigned, members, capacity)
    swapped = _balance_grad_ratio(candidates, assigned, members, profiles_by_name)

    assignments: list[Assignment] = []
    duplicate_count = 0

    for cand in candidates:
        team = assigned.get(cand.applicant_id)
        if team is None:
            continue

        tags = cand.tags_for(team)
        if cand.best_team is not None and team != cand.best_team:
            tags.append("OVERFLOW_REASSIGN")
        if cand.applicant_id in swapped:
            tags.append("GRAD_BALANCE")

        primary_score = cand.score_for(team)
        assignments.append(
            Assignment(
                applicant_id=cand.applicant_id,
                team_name=team,
                score=primary_score,
                tags=_ensure_min_tags(tags),
                is_duplicate=False,
                primary_team=None,
            )
        )

        # --- 5단계: 중복 배포 판정 ---
        if not allow_duplicate or primary_score <= 0:
            continue
        runner_up = next(
            ((name, score, t) for name, score, t in cand.options if name != team),
            None,
        )
        if runner_up is None:
            continue
        alt_team, alt_score, alt_tags = runner_up
        if alt_score <= 0:
            continue
        if alt_score / primary_score < duplicate_score_threshold:
            continue
        duplicate_count += 1
        assignments.append(
            Assignment(
                applicant_id=cand.applicant_id,
                team_name=alt_team,
                score=alt_score,
                tags=_ensure_min_tags([*alt_tags, "DUPLICATE_REVIEW"]),
                is_duplicate=True,
                primary_team=team,
            )
        )

    team_counts = {team: len(ids) for team, ids in sorted(members.items())}
    return DistributionResult(
        assignments=assignments,
        team_counts=team_counts,
        total_applicants=len(assigned),
        duplicate_count=duplicate_count,
        unassigned=unassigned,
        filtered_count=len(targets),
    )
