"""v4 — 2단계 계층적 배치 (4대 규칙 준수 우선)

Stage 1 : 팀 × 요일 배정 — 팀당 요일 2개
Stage 1b: 요일별 학사/대학원 쿼터 (규칙1)
Stage 2 : 팀-요일 그룹을 연속 시간대에 배치 (규칙3 세로 연속, 규칙4 첫 타임)

특성: 규칙 준수율은 높지만 팀당 수용량이 2요일 × 6타임 = 12슬롯으로 제한되어
커버리지가 약 68%까지 떨어진다. 이 trade-off를 v5가 해소한다.
"""
from __future__ import annotations

from app.domain.schemas import ApplicantIn, GenerateConstraints, InterviewerIn, PlanResult
from app.services import hierarchical
from app.services.board import Board

NAME = "v4"
DAYS_PER_TEAM = 2


def run(
    applicants: list[ApplicantIn],
    interviewers: list[InterviewerIn],
    constraints: GenerateConstraints | None = None,
    *,
    days_per_team: int = DAYS_PER_TEAM,
    fallback: bool = False,
) -> PlanResult:
    constraints = constraints or GenerateConstraints()
    target = constraints.grad_ratio_target
    board = Board(
        interviewers,
        max_daily_default=constraints.max_daily_default,
        pinned=constraints.pairs,
        pinned_by_team=constraints.pairs_by_team,
    )

    by_team = hierarchical.group_by_team(applicants)
    sizes = {team: len(members) for team, members in by_team.items()}

    # Stage 1
    team_days = hierarchical.assign_team_days(sizes, days_per_team)

    unassigned: list[ApplicantIn] = []
    for team, members in sorted(by_team.items()):
        days = team_days.get(team, [])
        # Stage 1b
        groups, leftover = hierarchical.split_team_by_day(members, days, target)
        unassigned.extend(leftover)
        # Stage 2
        for day in days:
            unassigned.extend(hierarchical.place_group(board, team, day, groups.get(day, [])))

    notes = {
        "days_per_team": days_per_team,
        "team_days": team_days,
        "stage3_fallback": fallback,
    }

    # Stage 3 (v5에서만 활성)
    if fallback and unassigned:
        _, unassigned = hierarchical.fallback_place(board, unassigned, target)

    return PlanResult(
        algorithm=NAME, assignments=board.assignments, unassigned=unassigned, notes=notes
    )
