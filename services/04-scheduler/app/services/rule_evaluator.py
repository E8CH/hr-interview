"""PPT 4대 배치 규칙 준수율 계산 (0~100점)

점수 정의
---------
rule1_grad_balance (SOFT) : 일차별 대학원 비율이 target±tolerance(기본 ±20%p)
                            안에 드는 날의 비율. 배정이 없는 날은 평가 제외.
                            target 을 안 주면 그 시간표에 실린 명단의 실제
                            비율 — 재는 것은 "요일 분산" 이지 3할이 아니다.
rule2_team_conflict (HARD): 같은 팀이 동시간에 중복 배치된 건수.
                            100 × (1 − 중복건수 / 전체 배정수).
rule3_vertical_group(SOFT): (팀, 면접일)별로 사용한 시간대가 HOURS 순서상 연속인 그룹의 비율.
                            Webex 방 재입장을 최소화하는 "세로 연속" 규칙.
rule4_first_slot   (SOFT): 오전 · 오후 첫 타임을 두 가지로 본다 — ① 첫 칸의 동시
                            진행 건수가 그 덩어리의 다른 칸보다 많지 않을 것, ②
                            첫 칸에 앉은 조가 그 덩어리의 조들보다 작을 것("첫
                            타임은 수요 적은 조부터"). 조의 크기는 그날 그 조가
                            보는 인원으로 잰다. 점유 시간대가 2개 미만인 덩어리는
                            평가 대상에서 제외.
overall                   : 네 점수의 산술 평균 (명세 예시 60/100/100/100 → 90.0과 일치).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.infrastructure.contracts import DAYS, HOURS, day_blocks, first_slots

RULE_KEYS = (
    "rule1_grad_balance",
    "rule2_team_conflict",
    "rule3_vertical_group",
    "rule4_first_slot",
)


def _get(obj: Any, name: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


@dataclass
class RuleReport:
    scores: dict[str, float] = field(default_factory=dict)
    details: dict[str, dict] = field(default_factory=dict)

    @property
    def overall(self) -> float:
        return self.scores.get("overall", 0.0)

    def flat(self) -> dict[str, float]:
        """POST /generate 응답용 — 점수만"""
        return dict(self.scores)

    def verbose(self) -> dict[str, dict]:
        """GET /rules 응답용 — {rule: {score, detail}}"""
        out: dict[str, dict] = {}
        for key in RULE_KEYS:
            out[key] = {"score": self.scores.get(key, 0.0), "detail": self.details.get(key, {})}
        out["overall"] = {"score": self.scores.get("overall", 0.0), "detail": {}}
        return out


def _round(value: float) -> float:
    return round(value + 1e-9, 1)


def rule1_grad_balance(assignments: Iterable[Any], target: float | None,
                       tolerance: float) -> tuple[float, dict]:
    """날마다 대학원 비율이 목표에서 크게 벗어나지 않는지.

    `target` 을 안 주면 **그 시간표에 실린 사람들의 실제 비율** 을 목표로 삼는다.
    원래 목적은 "학사 · 석박사 간 실력 편중이 생기지 않도록 요일을 분산" 하는
    것이다. 3할이라는 숫자를 지키는 것이 목적이 아니다 — 대학원생이 1할인
    회차는 어느 날도 3할이 될 수 없어 늘 0점이 나오고, 6할인 회차는 아무리
    고르게 나눠도 전부 위반으로 잡힌다. 어느 쪽이든 점수가 편중을 못 잰다.
    명단이 정한 비율을 기준으로 삼아야 '고르게 나눴는가' 를 재게 된다.
    """
    per_day_total: Counter = Counter()
    per_day_grad: Counter = Counter()
    for a in assignments:
        day = _get(a, "day")
        per_day_total[day] += 1
        if _get(a, "degree") == "대학원":
            per_day_grad[day] += 1

    total = sum(per_day_total.values())
    from_roster = target is None
    if from_roster:
        target = (sum(per_day_grad.values()) / total) if total else 0.0

    ratios: dict[str, float] = {}
    outside: list[str] = []
    low, high = target - tolerance, target + tolerance
    for day in DAYS:
        if per_day_total[day] == 0:
            continue
        ratio = per_day_grad[day] / per_day_total[day]
        ratios[day] = round(ratio, 4)
        if not (low - 1e-9 <= ratio <= high + 1e-9):
            outside.append(day)

    if not ratios:
        return 100.0, {"ratios": {}, "outside": [], "target": _round(target),
                       "tolerance": tolerance, "target_source": "명단" if from_roster else "지정"}

    score = 100.0 * (len(ratios) - len(outside)) / len(ratios)
    detail = {
        "ratios": ratios,
        "outside": outside,
        "target": round(target, 4),
        # 목표를 어디서 가져왔는지 — 화면이 "명단이 3.5할이라 그 언저리로 봅니다"
        # 라고 말할 수 있어야 사람이 점수를 믿는다.
        "target_source": "명단" if from_roster else "지정",
        "tolerance": tolerance,
        "acceptable_range": [round(low, 4), round(high, 4)],
    }
    return _round(score), detail


def rule2_team_conflict(assignments: Iterable[Any]) -> tuple[float, dict]:
    slots: Counter = Counter()
    total = 0
    for a in assignments:
        total += 1
        slots[(_get(a, "team"), _get(a, "day"), _get(a, "hour"))] += 1

    conflicts = [
        {"team": team, "day": day, "hour": hour, "count": count}
        for (team, day, hour), count in sorted(slots.items())
        if count > 1
    ]
    overlap = sum(c["count"] - 1 for c in conflicts)
    if total == 0:
        return 100.0, {"conflicts": [], "conflict_count": 0}
    score = max(0.0, 100.0 * (1 - overlap / total))
    return _round(score), {"conflicts": conflicts, "conflict_count": overlap}


def rule3_vertical_group(assignments: Iterable[Any]) -> tuple[float, dict]:
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for a in assignments:
        hour = _get(a, "hour")
        if hour not in HOURS:
            continue
        groups[(_get(a, "team"), _get(a, "day"))].append(HOURS.index(hour))

    if not groups:
        return 100.0, {"groups": 0, "contiguous": 0, "broken": []}

    broken = []
    contiguous = 0
    for (team, day), indices in sorted(groups.items()):
        uniq = sorted(set(indices))
        if (uniq[-1] - uniq[0]) == len(uniq) - 1:
            contiguous += 1
        else:
            broken.append({"team": team, "day": day, "hours": [HOURS[i] for i in uniq]})

    score = 100.0 * contiguous / len(groups)
    return _round(score), {"groups": len(groups), "contiguous": contiguous, "broken": broken}


def rule4_first_slot(assignments: Iterable[Any], timing=None) -> tuple[float, dict]:
    """오전 첫 타임 · 오후 첫 타임을 각각, 두 가지로 본다.

    ① 부하 — 첫 칸의 동시 진행 건수가 그 덩어리의 다른 칸보다 많지 않아야 한다.
    ② 구성 — 첫 칸에 앉은 조가 그 덩어리에 앉은 조들보다 작아야 한다. '수요 적은
       조부터' 가 여기서 판정된다. 조의 크기는 **그날 그 조가 보는 인원** 으로 잰다.
       지각 한 번의 손해는 그 조의 세로 줄이 얼마나 남았는지에 비례하기 때문이다.

    ①만 보면 규칙이 사실상 아무 말도 안 한다 — 칸마다 인원이 고르면 큰 조가 첫
    칸을 차지해도 만점이 나온다. ②가 그 경우를 잡는다. 반대로 ②만 보면 첫 칸에
    사람을 잔뜩 몰아넣어도 조 구성만 맞으면 통과한다. 둘을 따로 세고 함께 매긴다.

    어느 칸이 덩어리의 첫 칸인지는 면접 진행 조건이 정한다 — 30분 면접이면
    오후는 7타임(12:30)부터, 1시간 면접이면 4타임(12:30)부터다. 조건이 바뀌면
    판정 대상 칸도 함께 바뀌므로 `timing` 을 그대로 받아 계산한다.
    """
    blocks = day_blocks(timing)
    firsts = first_slots(timing)
    counts: Counter = Counter()
    team_at_first: dict[str, set[str]] = defaultdict(set)
    team_totals: Counter = Counter()
    team_day_size: Counter = Counter()
    teams_in_block: dict[tuple[str, int], set[str]] = defaultdict(set)
    for a in assignments:
        day, hour, team = _get(a, "day"), _get(a, "hour"), _get(a, "team")
        counts[(day, hour)] += 1
        team_totals[team] += 1
        team_day_size[(team, day)] += 1
        for index, block in enumerate(blocks):
            if hour in block:
                teams_in_block[(day, index)].add(team)
        if hour in firsts:
            team_at_first[f"{day}|{hour}"].add(team)

    def _mean_size(day: str, teams: Iterable[str]) -> float:
        sizes = [team_day_size[(team, day)] for team in teams]
        return sum(sizes) / len(sizes) if sizes else 0.0

    evaluated = 0
    passed = 0
    violations = []
    for day in DAYS:
        for index, block in enumerate(blocks):
            occupied = [h for h in block if counts[(day, h)] > 0]
            if len(occupied) < 2:
                continue  # 판정 불가 블록은 제외

            # ① 부하 — 첫 칸이 다른 칸보다 붐비지 않아야 한다
            evaluated += 1
            first = counts[(day, block[0])]
            rest = max(counts[(day, h)] for h in block[1:])
            if first <= rest:
                passed += 1
            else:
                violations.append(
                    {"day": day, "hour": block[0], "kind": "부하",
                     "first_count": first, "rest_max": rest}
                )

            # ② 구성 — 첫 칸에 앉은 조가 그 덩어리의 조들보다 작아야 한다.
            #    아무도 안 앉은 첫 칸은 잴 것이 없다.
            seated = team_at_first.get(f"{day}|{block[0]}") or set()
            if not seated:
                continue
            evaluated += 1
            here = _mean_size(day, seated)
            whole = _mean_size(day, teams_in_block[(day, index)])
            if here <= whole:
                passed += 1
            else:
                violations.append(
                    {"day": day, "hour": block[0], "kind": "구성",
                     "first_slot_avg": _round(here), "block_avg": _round(whole),
                     "teams": sorted(seated)}
                )

    # 그 칸에 앉은 조가 그날 몇 명짜리 조였는지 — '수요 적은 조부터' 를 사람이
    # 눈으로 확인할 수 있게 함께 적어 둔다.
    seated = {
        key: {team: team_day_size[(team, key.split("|")[0])] for team in sorted(teams)}
        for key, teams in sorted(team_at_first.items())
    }
    if evaluated == 0:
        return 100.0, {"evaluated": 0, "violations": [], "first_slot_teams": {},
                       "blocks": blocks, "first_slots": firsts}

    score = 100.0 * passed / evaluated
    detail = {
        "evaluated": evaluated,
        "passed": passed,
        "violations": violations,
        "blocks": blocks,
        "first_slots": firsts,
        "first_slot_teams": {k: sorted(v) for k, v in sorted(team_at_first.items())},
        "first_slot_team_sizes": seated,
        "team_totals": dict(sorted(team_totals.items())),
    }
    return _round(score), detail


def rule_compliance(
    assignments: Iterable[Any],
    interviewers: Iterable[Any] | None = None,
    applicants: Iterable[Any] | None = None,
    *,
    grad_ratio_target: float | None = None,
    grad_ratio_tolerance: float = 0.20,
    timing=None,
) -> RuleReport:
    """명세의 `rule_compliance(assignments, interviewers, applicants)` 구현

    `timing` 은 그 회차의 면접 진행 조건이다. 규칙4가 보는 '첫 타임' 이 몇 번째
    칸인지는 이 값으로 정해지므로, 시간표를 만들 때와 나중에 다시 검증할 때 같은
    값을 넘겨야 같은 점수가 나온다.

    `grad_ratio_target` 도 마찬가지다. 안 주면 그 시간표에 실린 명단의 실제
    비율로 규칙1을 잰다 — 편중을 재는 것이 목적이지 3할을 맞추는 것이 목적이
    아니기 때문이다. 인사가 숫자를 못 박았다면 만들 때와 검증할 때 같은 숫자를
    넘겨야 한다.
    """
    items = list(assignments)

    s1, d1 = rule1_grad_balance(items, grad_ratio_target, grad_ratio_tolerance)
    s2, d2 = rule2_team_conflict(items)
    s3, d3 = rule3_vertical_group(items)
    s4, d4 = rule4_first_slot(items, timing)

    overall = _round((s1 + s2 + s3 + s4) / 4)
    return RuleReport(
        scores={
            "rule1_grad_balance": s1,
            "rule2_team_conflict": s2,
            "rule3_vertical_group": s3,
            "rule4_first_slot": s4,
            "overall": overall,
        },
        details={
            "rule1_grad_balance": d1,
            "rule2_team_conflict": d2,
            "rule3_vertical_group": d3,
            "rule4_first_slot": d4,
        },
    )
