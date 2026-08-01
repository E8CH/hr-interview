"""중복면접자만 골라 다시 앉히는 손질 — 인사 화면의 '오류 수정하기'

두 팀이 같이 보기로 한 사람은 자리가 둘이다. 그 둘이 같은 시각이면 사람이
쪼개져야 하므로 시간표로 성립하지 않는다.

이런 자리는 부서에서 만들어져 온다. 인사가 1차로 보낸 명단에서 부서가 어떤
사람을 안 고르면 그 사람은 빠지고, 빈 자리는 다른 사람으로 메워진다. 그렇게
메운 자리가 하필 다른 팀이 같은 사람을 같은 시각에 잡아 둔 자리와 겹치는
것이다. 부서끼리는 서로의 시간표를 볼 수 없으니 부서에서는 알 길이 없다.

여기서는 겹친 사람만 옮긴다. 팀도 담당자도 부서가 정한 그대로 두고 시각만
바꾼다 — 겹치지도 않은 사람의 자리까지 흔들면, 부서가 확인하고 보낸 시간표가
인사 쪽에서 매번 다른 물건이 되어 버린다.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from app.infrastructure.contracts import DAYS, HOURS, day_name
from app.services.board import MAX_SLOTS_PER_DAY
from app.services.rule_evaluator import _get

#: 옮긴 자리에 남기는 사유 — 왜 부서가 보낸 시각이 아닌지 여기서만 설명된다
FIX_TAG = "DUP_FIXED"


def _row(index: int, item: Any) -> dict:
    return {
        "index": index,
        "applicant_id": _get(item, "applicant_id"),
        "applicant_name": _get(item, "applicant_name", "") or "",
        "team": _get(item, "team"),
        "interviewer_id": _get(item, "interviewer_id"),
        # 옛 회차는 날이 요일('월')로 저장돼 있다 — 읽을 때 일차로 맞춘다
        "day": day_name(_get(item, "day")),
        "hour": _get(item, "hour"),
        "lock_level": _get(item, "lock_level", "DRAFT") or "DRAFT",
    }


def rows_of(assignments: Iterable[Any]) -> list[dict]:
    return [_row(index, item) for index, item in enumerate(assignments)]


def conflicts(assignments: Iterable[Any]) -> list[dict]:
    """같은 사람이 같은 시각에 두 팀에 잡힌 자리 — 없으면 빈 목록.

    사람 한 명 · 한 시각이 한 건이다. 화면에는 이 숫자가 '고칠 곳' 으로 나간다.
    """
    rows = rows_of(assignments)
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["applicant_id"], row["day"], row["hour"])].append(row)
    found = []
    for (applicant_id, day, hour), mine in sorted(grouped.items()):
        teams = {row["team"] for row in mine}
        if len(mine) < 2 or len(teams) < 2:
            continue
        found.append(
            {
                "applicant_id": applicant_id,
                "applicant_name": mine[0]["applicant_name"],
                "day": day,
                "hour": hour,
                "teams": sorted(teams),
                "message": f"{applicant_id} {day} {hour} 두 팀 면접이 같은 시각"
                           f" ({', '.join(sorted(teams))})",
            }
        )
    return found


class _Space:
    """지금 시간표가 이미 쓰고 있는 자리 — 옮길 칸을 고를 때 보는 판."""

    def __init__(self, rows: list[dict], interviewers: Iterable[Any] | None,
                 ignore_availability: bool = False) -> None:
        self.ignore_availability = bool(ignore_availability)
        self.by_id: dict[str, Any] = {
            _get(iv, "interviewer_id"): iv for iv in (interviewers or [])
        }
        # 겹친 자리는 같은 칸을 둘이 쓰고 있다. 하나를 빼도 나머지 하나는 남아
        # 있어야 하므로 '있다 · 없다' 가 아니라 몇 건인지로 센다 — 집합으로 두면
        # 옮길 사람을 빼는 순간 그 칸이 비어 보여서 제자리에 도로 앉힌다.
        self.team_slot: Counter = Counter()
        self.iv_slot: Counter = Counter()
        self.applicant_slot: Counter = Counter()
        self.iv_day: Counter = Counter()
        for row in rows:
            self.take(row)

    def take(self, row: dict) -> None:
        self.team_slot[(row["team"], row["day"], row["hour"])] += 1
        self.iv_slot[(row["interviewer_id"], row["day"], row["hour"])] += 1
        self.applicant_slot[(row["applicant_id"], row["day"], row["hour"])] += 1
        self.iv_day[(row["interviewer_id"], row["day"])] += 1

    def free(self, row: dict) -> None:
        self.team_slot[(row["team"], row["day"], row["hour"])] -= 1
        self.iv_slot[(row["interviewer_id"], row["day"], row["hour"])] -= 1
        self.applicant_slot[(row["applicant_id"], row["day"], row["hour"])] -= 1
        self.iv_day[(row["interviewer_id"], row["day"])] -= 1

    def cap(self, interviewer_id: str) -> int:
        iv = self.by_id.get(interviewer_id)
        limit = int(_get(iv, "max_daily", MAX_SLOTS_PER_DAY) or MAX_SLOTS_PER_DAY) if iv else MAX_SLOTS_PER_DAY
        return min(limit, MAX_SLOTS_PER_DAY)

    def available(self, interviewer_id: str, day: str, hour: str) -> bool:
        if self.ignore_availability:
            return True
        iv = self.by_id.get(interviewer_id)
        if iv is None:
            return True     # 명단에 없는 담당자는 여기서 더 좁힐 근거가 없다
        hours = (_get(iv, "availability", {}) or {}).get(day) or []
        return hour in hours

    def why_not(self, row: dict, day: str, hour: str) -> str | None:
        """그 칸에 못 앉는 까닭 — 앉을 수 있으면 None."""
        interviewer_id = row["interviewer_id"]
        if self.team_slot[(row["team"], day, hour)] > 0:
            return "TEAM_TAKEN"
        if self.applicant_slot[(row["applicant_id"], day, hour)] > 0:
            return "APPLICANT_TAKEN"
        if self.iv_slot[(interviewer_id, day, hour)] > 0:
            return "IV_BUSY"
        if not self.available(interviewer_id, day, hour):
            return "IV_OFF"
        if self.iv_day[(interviewer_id, day)] >= self.cap(interviewer_id):
            return "IV_CAP"
        return None


def _team_days(days_by_team: dict | None, team: str) -> list[str]:
    """그 팀이 면접을 보기로 한 날 — 모르면 닷새 전부."""
    given = [day_name(d) for d in ((days_by_team or {}).get(team) or [])]
    days = [d for d in given if d in DAYS]
    return days or list(DAYS)


def _nearest(space: _Space, row: dict, days: list[str]) -> tuple[str, str] | None:
    """지금 자리에서 가장 가까운 빈 칸 — 그 팀 면접일 안에서만 찾는다."""
    here_day = DAYS.index(row["day"]) if row["day"] in DAYS else 0
    here_hour = HOURS.index(row["hour"]) if row["hour"] in HOURS else 0
    open_slots = [
        (abs(DAYS.index(day) - here_day), abs(HOURS.index(hour) - here_hour), day, hour)
        for day in days
        for hour in HOURS
        if space.why_not(row, day, hour) is None
    ]
    if not open_slots:
        return None
    _dd, _dh, day, hour = min(open_slots)
    return day, hour


def _movable(mine: list[dict]) -> list[dict]:
    """겹친 자리 중 옮길 쪽 — 한 자리는 남긴다.

    확정(락)해 둔 자리는 건드리지 않는다. 둘 다 확정이 아니면 팀 이름 순으로
    앞의 한 자리를 그대로 두고 나머지를 옮긴다 — 어느 쪽을 남길지는 우열이
    없으므로, 같은 시간표를 두 번 고쳐도 같은 결과가 나오는 쪽을 고른다.
    """
    ordered = sorted(mine, key=lambda row: (row["lock_level"] != "LOCKED", row["team"]))
    return ordered[1:]


def plan_fix(
    assignments: Iterable[Any],
    interviewers: Iterable[Any] | None = None,
    days_by_team: dict | None = None,
    ignore_availability: bool = False,
) -> tuple[list[dict], list[dict]]:
    """겹친 사람만 옮긴 계획 — (옮긴 자리, 못 옮긴 자리).

    시간표 자체는 건드리지 않는다. 부르는 쪽이 이 계획을 보고 저장한다.
    """
    rows = rows_of(assignments)
    space = _Space(rows, interviewers, ignore_availability)
    by_index = {row["index"]: row for row in rows}

    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["applicant_id"], row["day"], row["hour"])].append(row)

    moved: list[dict] = []
    stuck: list[dict] = []
    for key, mine in sorted(grouped.items()):
        if len(mine) < 2 or len({row["team"] for row in mine}) < 2:
            continue
        for row in _movable(mine):
            if row["lock_level"] == "LOCKED":
                stuck.append({**_where(row), "reason": "LOCKED"})
                continue
            space.free(row)
            found = _nearest(space, row, _team_days(days_by_team, row["team"]))
            if found is None:
                space.take(row)     # 되돌려 놓는다 — 못 옮기면 원래 자리에 남는다
                stuck.append({**_where(row), "reason": "NO_ROOM"})
                continue
            day, hour = found
            before = (row["day"], row["hour"])
            row["day"], row["hour"] = day, hour
            space.take(row)
            by_index[row["index"]] = row
            moved.append(
                {
                    "index": row["index"],
                    "applicant_id": row["applicant_id"],
                    "applicant_name": row["applicant_name"],
                    "team": row["team"],
                    "interviewer_id": row["interviewer_id"],
                    "from_day": before[0], "from_hour": before[1],
                    "day": day, "hour": hour,
                }
            )
    return moved, stuck


def _where(row: dict) -> dict:
    return {
        "index": row["index"],
        "applicant_id": row["applicant_id"],
        "applicant_name": row["applicant_name"],
        "team": row["team"],
        "interviewer_id": row["interviewer_id"],
        "day": row["day"],
        "hour": row["hour"],
    }
