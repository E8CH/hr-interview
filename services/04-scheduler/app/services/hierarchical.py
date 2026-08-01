"""v4·v5가 공유하는 계층적 배치 스테이지

Stage 1  : 팀 × 면접일 배정 (어느 팀이나 1일차부터)
Stage 1b : 일차별 학사/대학원 쿼터 분배 (규칙1)
Stage 2  : 팀-면접일 그룹을 연속 시간대에 배치 (규칙3·규칙4)
Stage 3  : Fallback 흡수 — v5 전용, 미배정자를 규칙 손해가 가장 적은 슬롯에 밀어 넣음
"""
from __future__ import annotations

from collections import defaultdict

from app.domain.schemas import ApplicantIn, PlannedAssignment
from app.infrastructure.contracts import DAYS, HOURS, first_slots, plan_team_days
from app.services.board import Board

SLOTS_PER_DAY = len(HOURS)

#: 그날 이 인원보다 적게 보는 조를 '수요 적은 조' 로 본다 — 첫 타임을 먼저 준다.
SMALL_GROUP = 4


# --------------------------------------------------------------------------
# Stage 1 — 팀 × 면접일
# --------------------------------------------------------------------------
def assign_team_days(sizes_by_team: dict[str, int], days_per_team: int) -> dict[str, list[str]]:
    """어느 팀이나 1일차부터 days_per_team일씩 — 팀마다 같은 날을 쓴다.

    셈은 `shared/contracts` 의 `plan_team_days` 하나로 모았다 — 인사 화면도 부서
    화면도 같은 답을 알아야 그 팀이 며칟날까지 보는지 말할 수 있기 때문이다.
    """
    return plan_team_days(sizes_by_team, days_per_team)


def fixed_team_days(
    given: dict[str, list[str]] | None, sizes_by_team: dict[str, int], days_per_team: int
) -> dict[str, list[str]]:
    """인사가 명단을 보낼 때 정해 둔 날을 그대로 쓴다 — 없는 팀만 새로 뽑는다.

    부서가 자리를 잡을 때 이미 이 날들을 보고 잡았으므로, 여기서 다시 뽑으면
    부서가 본 시간표와 최종 시간표가 어긋난다. 넘어온 이름 중 달력에 없는
    것은 버린다.
    """
    planned = assign_team_days(sizes_by_team, days_per_team)
    if not given:
        return planned
    result: dict[str, list[str]] = {}
    for team in sizes_by_team:
        days = [d for d in (given.get(team) or []) if d in DAYS]
        # 같은 날을 두 번 적어 보냈으면 한 번만 센다 — 자리 수가 부풀지 않게
        seen: list[str] = []
        for day in days:
            if day not in seen:
                seen.append(day)
        result[team] = seen or planned.get(team, [])
    return result


# --------------------------------------------------------------------------
# Stage 1b — 일차별 학사/대학원 쿼터
# --------------------------------------------------------------------------
def balanced_sizes(total: int, n_days: int, cap: int = SLOTS_PER_DAY) -> list[int]:
    """total명을 n_days개 면접일에 cap 한도로 균등 분배"""
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
    applicants: list[ApplicantIn], days: list[str], target_ratio: float | None = None
) -> tuple[dict[str, list[ApplicantIn]], list[ApplicantIn]]:
    """팀 지원자를 배정된 면접일별로 나누고 대학원 쿼터를 맞춘다.

    `target_ratio` 를 안 주면 **그 팀 명단의 실제 비율** 을 목표로 삼는다. 하려는
    일은 "학사 · 석박사 편중이 생기지 않도록 요일을 분산" 하는 것이므로, 팀이
    가진 만큼을 날마다 고르게 나누면 된다. 3할 같은 고정값을 들이대면 대학원생이
    한 명뿐인 팀은 그 한 명을 어느 날에 넣어도 목표를 못 맞추고, 대학원생만 있는
    팀은 아예 맞출 길이 없다 — 그 팀들의 쿼터가 뜻 없이 한쪽으로 쏠린다.

    반환: ({면접일: [지원자]}, 수용량 초과로 남은 지원자)
    """
    if not days:
        return {}, list(applicants)

    grads = sorted([a for a in applicants if a.is_grad], key=lambda a: (-a.priority_score, a.applicant_id))
    bachelors = sorted(
        [a for a in applicants if not a.is_grad], key=lambda a: (-a.priority_score, a.applicant_id)
    )
    if target_ratio is None:
        target_ratio = (len(grads) / len(applicants)) if applicants else 0.0

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
# Stage 0 — 부서가 잡아 둔 자리
# --------------------------------------------------------------------------
def place_dept_seats(
    board: Board, team: str, days: list[str], members: list[ApplicantIn]
) -> list[ApplicantIn]:
    """부서가 자기 시간표에서 잡아 둔 자리에 먼저 앉힌다. 남은 사람을 돌려준다.

    부서 화면은 하루 몇 칸씩 끊어 '1일차 · 2일차' 로만 센다. 그 팀이 며칟날까지
    보는지는 인사팀이 Stage 1 에서 정하므로, 부서가 말한 n일차를 그 팀에 잡힌
    날 중 n 번째로 옮겨 읽는다. 부서가 본 순서와 시각은 그대로 남는다.

    앉히지 못한 사람은 왜 못 앉혔는지를 보드에 적어 두고, **그 자리에서 가장
    가까운 빈 칸** 으로만 옮긴다. 팀 시간표를 통째로 다시 짜면 자리 하나가 막힌
    탓에 멀쩡히 앉을 수 있던 사람들까지 줄줄이 밀린다 — 부서가 확인하고 보낸
    시간표와 최종본이 매번 달라 보이던 까닭이다. 가까운 칸도 없을 때만 다음
    단계로 넘긴다.

    두 번에 나눠 도는 이유: 먼저 **모두를 제 자리에** 앉히고, 그 뒤에 못 앉은
    사람만 옮긴다. 한 사람씩 옮겨 가며 채우면 먼저 밀린 사람이 뒷사람의 제
    자리를 차지해 버려, 원래 지킬 수 있던 자리까지 연쇄로 어긋난다.
    """
    if not days:
        return list(members)
    left: list[ApplicantIn] = []
    moved: list[ApplicantIn] = []
    for applicant in members:
        wish = board.seat_for(team, applicant.applicant_id)
        if wish is None:
            left.append(applicant)
            continue
        day_no, slot_no = wish
        if not 1 <= day_no <= len(days) or not 0 <= slot_no < SLOTS_PER_DAY:
            # 부서가 인사팀이 잡은 면접일 수보다 긴 시간표를 짰다
            board.seat_miss[applicant.applicant_id] = "SEAT_MOVED_DAY"
            moved.append(applicant)
            continue
        day, hour = days[day_no - 1], HOURS[slot_no]
        tags = list(applicant.tags or ["PRIMARY_JOB"]) + ["DEPT_SEAT"]
        if board.place(applicant, day, hour, tags) is None:
            board.seat_miss[applicant.applicant_id] = board.seat_miss_reason(
                team, applicant.applicant_id, day, hour
            )
            moved.append(applicant)

    for applicant in moved:
        if not _place_nearest(board, team, days, applicant):
            left.append(applicant)
    return left


def _place_nearest(
    board: Board, team: str, days: list[str], applicant: ApplicantIn
) -> bool:
    """부서가 잡아 준 자리에서 가장 가까운 빈 칸에 앉힌다 — 없으면 False.

    가깝다는 기준은 ① 같은 날 ② 칸 번호 차이다. 부서는 '오전 중에 몰아서'
    처럼 자리 순서에 뜻을 담아 보내므로, 날을 옮기는 것보다 같은 날 옆 칸으로
    미는 편이 부서 결정을 덜 헤친다.
    """
    wish = board.seat_for(team, applicant.applicant_id)
    day_no, slot_no = wish if wish else (1, 0)
    tags = list(applicant.tags or ["PRIMARY_JOB"])
    candidates = [
        (abs(index + 1 - day_no), abs(HOURS.index(hour) - slot_no), index, hour)
        for index, day in enumerate(days)
        for hour in HOURS
        if board.can_place(team, day, hour, applicant.applicant_id)
    ]
    for _dd, _dh, index, hour in sorted(candidates):
        if board.place(applicant, days[index], hour, tags) is not None:
            return True
    return False


# --------------------------------------------------------------------------
# Stage 2 — 시간대 세부 최적화
# --------------------------------------------------------------------------
def place_group(
    board: Board, team: str, day: str, group: list[ApplicantIn],
    extra_tags: list[str] | None = None, timing=None
) -> list[ApplicantIn]:
    """팀-면접일 그룹을 연속 시간대에 배치. 배치 실패한 지원자를 반환."""
    if not group:
        return []

    k = len(group)
    if k > SLOTS_PER_DAY:
        group, overflow = group[:SLOTS_PER_DAY], group[SLOTS_PER_DAY:]
        k = len(group)
    else:
        overflow = []

    # 첫 칸(오전 · 오후)은 그날 적게 보는 조부터 앉힌다 — 규칙4 "첫 타임은 수요
    # 적은 조". 첫 타임에 지각이 나면 그 조의 세로 줄이 통째로 밀리므로, 뒤에
    # 남은 줄이 짧은 조가 앉아야 손해가 작다. 그래서 3명 이하 그룹은 첫 칸이
    # 든 창을 먼저 고른다.
    #
    # 큰 조를 억지로 비껴 앉히지는 않는다. 다 같이 첫 칸을 피하면 09시가 통째로
    # 비어 하루가 한 칸씩 늦게 끝난다. 큰 조가 첫 칸에 앉는 것 자체는 손해가
    # 아니고, 작은 조를 제쳐 두고 앉는 것이 손해다 — 그건 위 우선순위로 막힌다.
    #
    # 첫 칸이 몇 번째인지는 면접 진행 조건이 정한다 — 30분 면접이면 오전 1타임 ·
    # 오후 7타임, 1시간 면접이면 오전 1타임 · 오후 4타임이다.
    guarded = {HOURS.index(hour) for hour in first_slots(timing) if hour in HOURS}
    small = k < SMALL_GROUP
    starts = sorted(
        range(SLOTS_PER_DAY - k + 1),
        key=lambda s: (0 if small and guarded & set(range(s, s + k)) else 1, s),
    )

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
        if board.place(applicant, day, hour, tags) is not None:
            continue
        # 칸은 이 팀에게 비어 있는데 그 사람만 못 앉는 경우 — 두 팀이 같이 보는
        # 사람이 다른 팀 면접에 이미 그 시각으로 잡혀 있을 때다. 모든 팀이 같은
        # 날부터 면접을 보므로 이 겹침은 드물지 않다. 그룹 전체를 흔들지 않고
        # 그 사람만 같은 날 가장 가까운 빈 칸으로 민다.
        near = sorted(
            (h for h in HOURS
             if h not in chosen and board.can_place(team, day, h, applicant.applicant_id)),
            key=lambda h: abs(HOURS.index(h) - HOURS.index(hour)),
        )
        if not any(board.place(applicant, day, h, tags) is not None for h in near):
            unplaced.append(applicant)
    # 연속 창이 그룹보다 짧았던 경우
    if len(chosen) < k:
        unplaced.extend(group[len(chosen) :])
    return unplaced


# --------------------------------------------------------------------------
# Stage 3 — Fallback (v5)
# --------------------------------------------------------------------------
def fallback_place(
    board: Board, leftovers: list[ApplicantIn], target_ratio: float | None = None, timing=None
) -> tuple[list[PlannedAssignment], list[ApplicantIn]]:
    """미배정자를 규칙 손해가 최소인 슬롯에 흡수한다.

    후보 슬롯 비용:
      +100  세로 연속(규칙3)을 깨뜨림
      + 12  그날 이미 길게 잡은 조가 첫 타임(오전 · 오후)에 더 앉는 경우 (규칙4)
      +  ρ  배치 후 그날 대학원 비율의 목표 이탈도 (규칙1)

    `target_ratio` 를 안 주면 **이 회차에 앉은 사람 + 아직 못 앉은 사람** 의 실제
    비율을 목표로 삼는다. 여기서는 팀을 가리지 않고 날을 고르므로, 팀별이 아니라
    회차 전체의 비율이 '고르게 나눈 상태' 다. 규칙1을 매길 때와 같은 잣대여야
    이 단계가 점수를 스스로 깎지 않는다.
    """
    if target_ratio is None:
        pool = sum(board.day_count.values()) + len(leftovers)
        grads = sum(board.day_grad.values()) + sum(1 for a in leftovers if a.is_grad)
        target_ratio = (grads / pool) if pool else 0.0
    guarded = set(first_slots(timing))
    placed: list[PlannedAssignment] = []
    still_unassigned: list[ApplicantIn] = []

    for applicant in sorted(leftovers, key=lambda a: (-a.priority_score, a.applicant_id)):
        best_key: tuple[float, int, int] | None = None
        best_slot: tuple[str, str] | None = None
        for day in DAYS:
            for hour in HOURS:
                if not board.can_place(applicant.team, day, hour, applicant.applicant_id):
                    continue
                cost = 0.0
                if board.would_break_contiguity(applicant.team, day, hour):
                    cost += 100.0
                # 첫 타임은 그날 줄이 짧은 조에게 양보한다 — 이미 길게 잡은
                # 조가 여기까지 앉으면 지각 한 번에 밀릴 줄이 그만큼 길어진다.
                line = len(board.team_day_hours.get((applicant.team, day), ()))
                if hour in guarded and line >= SMALL_GROUP:
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
