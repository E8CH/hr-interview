"""v4·v5가 공유하는 계층적 배치 스테이지

Stage 1  : 팀 × 요일 배정 (요일 부하 균등)
Stage 1b : 요일별 학사/대학원 쿼터 분배 (규칙1)
Stage 2  : 팀-요일 그룹을 연속 시간대에 배치 (규칙3·규칙4)
Stage 3  : Fallback 흡수 — v5 전용, 미배정자를 규칙 손해가 가장 적은 슬롯에 밀어 넣음
"""
from __future__ import annotations

from collections import Counter, defaultdict
from math import ceil

from app.domain.schemas import ApplicantIn, PlannedAssignment
from app.infrastructure.contracts import DAYS, HOURS
from app.services.board import Board
from app.services.rule_evaluator import FIRST_SLOTS

SLOTS_PER_DAY = len(HOURS)


# --------------------------------------------------------------------------
# Stage 1 — 팀 × 요일
# --------------------------------------------------------------------------
def assign_team_days(sizes_by_team: dict[str, int], days_per_team: int) -> dict[str, list[str]]:
    """큰 팀부터 부하가 가장 낮은 요일을 골라 days_per_team개씩 배정"""
    day_load: Counter = Counter()
    result: dict[str, list[str]] = {}
    ordered = sorted(sizes_by_team.items(), key=lambda kv: (-kv[1], kv[0]))

    for team, size in ordered:
        chosen = sorted(DAYS, key=lambda d: (day_load[d], DAYS.index(d)))[:days_per_team]
        chosen.sort(key=DAYS.index)
        per_day = min(SLOTS_PER_DAY, ceil(size / days_per_team)) if days_per_team else 0
        for day in chosen:
            day_load[day] += per_day
        result[team] = chosen
    return result


# --------------------------------------------------------------------------
# Stage 1b — 요일별 학사/대학원 쿼터
# --------------------------------------------------------------------------
def balanced_sizes(total: int, n_days: int, cap: int = SLOTS_PER_DAY) -> list[int]:
    """total명을 n_days개 요일에 cap 한도로 균등 분배"""
    if n_days <= 0:
        return []
    sizes = [0] * n_days
    remaining = min(total, n_days * cap)
    idx = 0
    while remaining > 0:
        slot = idx % n_days
        if sizes[slot] < cap:
            sizes[slot] += 1
            remaining -= 1
        idx += 1
    return sizes


def _largest_remainder(desired: list[float], total: int, caps: list[int]) -> list[int]:
    base = [min(int(d), c) for d, c in zip(desired, caps)]
    rem = total - sum(base)
    order = sorted(range(len(desired)), key=lambda i: (-(desired[i] - int(desired[i])), i))

    guard = 0
    while rem > 0 and guard < 1000:
        progressed = False
        for i in order:
            if rem == 0:
                break
            if base[i] < caps[i]:
                base[i] += 1
                rem -= 1
                progressed = True
        if not progressed:
            break
        guard += 1
    guard = 0
    while rem < 0 and guard < 1000:
        progressed = False
        for i in reversed(order):
            if rem == 0:
                break
            if base[i] > 0:
                base[i] -= 1
                rem += 1
                progressed = True
        if not progressed:
            break
        guard += 1
    return base


def split_team_by_day(
    applicants: list[ApplicantIn], days: list[str], target_ratio: float
) -> tuple[dict[str, list[ApplicantIn]], list[ApplicantIn]]:
    """팀 지원자를 배정 요일별로 나누고 대학원 쿼터를 맞춘다.

    반환: ({요일: [지원자]}, 수용량 초과로 남은 지원자)
    """
    if not days:
        return {}, list(applicants)

    grads = sorted([a for a in applicants if a.is_grad], key=lambda a: (-a.priority_score, a.applicant_id))
    bachelors = sorted(
        [a for a in applicants if not a.is_grad], key=lambda a: (-a.priority_score, a.applicant_id)
    )

    sizes = balanced_sizes(len(applicants), len(days))
    take = sum(sizes)

    grad_take = min(round(take * target_ratio), len(grads))
    if take - grad_take > len(bachelors):
        grad_take = take - len(bachelors)
    grad_take = max(0, min(grad_take, len(grads), take))

    desired = [s * target_ratio for s in sizes]
    quotas = _largest_remainder(desired, grad_take, sizes)

    by_day: dict[str, list[ApplicantIn]] = {}
    g_cursor = 0
    b_cursor = 0
    for day, size, quota in zip(days, sizes, quotas):
        group: list[ApplicantIn] = []
        for _ in range(quota):
            if g_cursor < len(grads):
                group.append(grads[g_cursor])
                g_cursor += 1
        while len(group) < size and b_cursor < len(bachelors):
            group.append(bachelors[b_cursor])
            b_cursor += 1
        while len(group) < size and g_cursor < len(grads):
            group.append(grads[g_cursor])
            g_cursor += 1
        by_day[day] = group

    leftover = grads[g_cursor:] + bachelors[b_cursor:]
    leftover.sort(key=lambda a: (-a.priority_score, a.applicant_id))
    return by_day, leftover


# --------------------------------------------------------------------------
# Stage 2 — 시간대 세부 최적화
# --------------------------------------------------------------------------
def place_group(
    board: Board, team: str, day: str, group: list[ApplicantIn], extra_tags: list[str] | None = None
) -> list[ApplicantIn]:
    """팀-요일 그룹을 연속 시간대에 배치. 배치 실패한 지원자를 반환."""
    if not group:
        return []

    k = len(group)
    if k > SLOTS_PER_DAY:
        group, overflow = group[:SLOTS_PER_DAY], group[SLOTS_PER_DAY:]
        k = len(group)
    else:
        overflow = []

    # 소규모 그룹(3명 이하)은 첫 타임(09시)을 피해 시작 → 규칙4 "첫 타임 소규모"
    prefer = 0 if k >= 4 else 1
    starts = sorted(range(SLOTS_PER_DAY - k + 1), key=lambda s: (abs(s - prefer), s))

    chosen: list[str] | None = None
    for start in starts:
        window = HOURS[start : start + k]
        if all(board.team_slot_free(team, day, h) for h in window):
            chosen = window
            break
    if chosen is None:
        chosen = [h for h in HOURS if board.team_slot_free(team, day, h)][:k]

    unplaced: list[ApplicantIn] = list(overflow)
    tags_extra = extra_tags or []
    for applicant, hour in zip(group, chosen):
        tags = list(applicant.tags or ["PRIMARY_JOB"]) + tags_extra
        if board.place(applicant, day, hour, tags) is None:
            unplaced.append(applicant)
    # 연속 창이 그룹보다 짧았던 경우
    if len(chosen) < k:
        unplaced.extend(group[len(chosen) :])
    return unplaced


# --------------------------------------------------------------------------
# Stage 3 — Fallback (v5)
# --------------------------------------------------------------------------
def fallback_place(
    board: Board, leftovers: list[ApplicantIn], target_ratio: float
) -> tuple[list[PlannedAssignment], list[ApplicantIn]]:
    """미배정자를 규칙 손해가 최소인 슬롯에 흡수한다.

    후보 슬롯 비용:
      +100  세로 연속(규칙3)을 깨뜨림
      + 12  비어 있던 첫 타임을 새로 여는 경우 (규칙4)
      +  ρ  배치 후 해당 요일 대학원 비율의 목표 이탈도 (규칙1)
    """
    placed: list[PlannedAssignment] = []
    still_unassigned: list[ApplicantIn] = []

    for applicant in sorted(leftovers, key=lambda a: (-a.priority_score, a.applicant_id)):
        best_key: tuple[float, int, int] | None = None
        best_slot: tuple[str, str] | None = None
        for day in DAYS:
            for hour in HOURS:
                if not board.can_place(applicant.team, day, hour):
                    continue
                cost = 0.0
                if board.would_break_contiguity(applicant.team, day, hour):
                    cost += 100.0
                if hour in FIRST_SLOTS and board.day_count[day] > 0:
                    cost += 12.0
                total = board.day_count[day] + 1
                grad = board.day_grad[day] + (1 if applicant.is_grad else 0)
                cost += abs(grad / total - target_ratio) * 60.0
                cost += board.day_count[day] * 0.01
                key = (round(cost, 6), DAYS.index(day), HOURS.index(hour))
                if best_key is None or key < best_key:
                    best_key, best_slot = key, (day, hour)
        if best_slot is None:
            still_unassigned.append(applicant)
            continue
        day, hour = best_slot
        tags = list(applicant.tags or ["PRIMARY_JOB"]) + ["HR_MANUAL"]
        assignment = board.place(applicant, day, hour, tags)
        if assignment is None:
            still_unassigned.append(applicant)
        else:
            placed.append(assignment)

    return placed, still_unassigned


def group_by_team(applicants: list[ApplicantIn]) -> dict[str, list[ApplicantIn]]:
    grouped: dict[str, list[ApplicantIn]] = defaultdict(list)
    for a in applicants:
        grouped[a.team].append(a)
    return dict(grouped)
