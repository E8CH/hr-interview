"""v4 — 2단계 계층적 배치 (4대 규칙 준수 우선)

Stage 1 : 팀 × 요일 배정 — 팀당 요일 2개
Stage 1b: 요일별 학사/대학원 쿼터 (규칙1)
Stage 2 : 팀-요일 그룹을 연속 시간대에 배치 (규칙3 세로 연속, 규칙4 첫 타임)

특성: 규칙 준수율은 높지만 팀당 수용량이 2요일 × 8타임 = 16슬롯으로 제한되어
커버리지가 떨어진다. 이 trade-off를 v5가 해소한다.
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
        ignore_availability=constraints.ignore_availability,
        seats=constraints.seat_map(),
    )

    by_team = hierarchical.group_by_team(applicants)
    sizes = {team: len(members) for team, members in by_team.items()}

    # Stage 1 — 인사가 명단을 보낼 때 정해 둔 요일이 있으면 그대로 쓴다
    team_days = hierarchical.fixed_team_days(
        constraints.days_by_team, sizes, days_per_team
    )

    unassigned: list[ApplicantIn] = []
    honoured = 0
    for team, members in sorted(by_team.items()):
        days = team_days.get(team, [])
        # Stage 0 — 부서가 잡아 둔 자리를 먼저 지킨다. 부서 시간표가 최종
        # 시간표의 입력이 되도록, 나머지 단계는 남은 사람만 다시 짠다.
        before = len(board.assignments)
        members = hierarchical.place_dept_seats(board, team, days, members)
        honoured += len(board.assignments) - before
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
        # 부서가 보낸 자리 중 그대로 지켜진 수 · 옮긴 사람과 그 까닭
        "dept_seats": sum(len(wish) for wish in board.seats.values()),
        "dept_seats_kept": honoured,
        "dept_seats_moved": dict(board.seat_miss),
    }

    # Stage 3 (v5에서만 활성)
    if fallback and unassigned:
        _, unassigned = hierarchical.fallback_place(board, unassigned, target)

    return PlanResult(
        algorithm=NAME, assignments=board.assignments, unassigned=unassigned, notes=notes
    )
