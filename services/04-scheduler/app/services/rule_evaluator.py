"""PPT 4대 배치 규칙 준수율 계산 (0~100점)

점수 정의
---------
rule1_grad_balance (SOFT) : (팀, 면접일)마다의 대학원 비율이 그 팀의 비율
                            ±tolerance(기본 ±20%p) 안에 드는 칸의 비율.
                            면접일이 하루뿐인 팀은 나눌 것이 없어 평가 제외.
                            target 을 안 주면 그 팀 명단의 실제 비율 — 재는 것은
                            "요일 분산" 이지 3할이 아니다.
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
    """**팀마다** 대학원생이 특정 면접일로 몰리지 않았는지.

    원래 목적은 "학사 · 석박사 간 실력 편중이 생기지 않도록 요일을 분산" 하는
    것이다. 편중은 **팀 안에서** 생긴다 — 한 팀의 대학원생이 죄다 1일차에
    앉으면 그 날은 저희끼리만 비교하는 자리가 되고, 2일차는 학사끼리만 겨루는
    자리가 된다. 지원자를 뽑는 것도 견주는 것도 팀 안에서 일어나므로, 다른 팀이
    그 날을 어떻게 채웠는지는 이 사람의 처지를 바꾸지 않는다.

    그래서 (팀, 면접일)마다 잰다. 회차 전체의 날별 비율은 **팀마다 면접일이
    다르면 깨지는 대리 지표**다 — 어느 팀이나 자기 날들에 고르게 나뉘었는데도,
    한 팀의 넘친 인원만 앉는 날이 있으면 그 날 비율이 혼자 튀어 점수가 깎인다.
    고칠 수 없는 것을 벌하는 점수는 사람이 곧 안 믿게 된다.

    `target` 을 안 주면 **그 팀 명단의 실제 비율** 이 목표다. 3할 같은 고정값을
    지키는 것이 목적이 아니다 — 대학원생만 있는 팀은 어느 날도 3할이 될 수 없다.

    면접일이 **하루뿐인 팀은 평가에서 뺀다.** 나눌 날이 없으면 편중을 만들
    수도 없앨 수도 없다(규칙3 · 규칙4가 판정 불가를 빼는 것과 같은 뜻이다).
    """
    per_cell_total: Counter = Counter()
    per_cell_grad: Counter = Counter()
    per_day_total: Counter = Counter()
    per_day_grad: Counter = Counter()
    teams: dict[str, list[str]] = defaultdict(list)
    for a in assignments:
        day, team = _get(a, "day"), _get(a, "team")
        is_grad = _get(a, "degree") == "대학원"
        per_cell_total[(team, day)] += 1
        per_day_total[day] += 1
        if is_grad:
            per_cell_grad[(team, day)] += 1
            per_day_grad[day] += 1
        if day not in teams[team]:
            teams[team].append(day)

    from_roster = target is None
    grand_total = sum(per_day_total.values())
    round_ratio = (sum(per_day_grad.values()) / grand_total) if grand_total else 0.0

    team_ratios: dict[str, dict] = {}
    outside: list[dict] = []
    single_day: list[str] = []
    evaluated = 0
    for team in sorted(teams):
        days = [d for d in DAYS if d in teams[team]] or sorted(teams[team])
        seated = sum(per_cell_total[(team, d)] for d in days)
        if len(days) < 2:
            # 하루뿐인 팀 — 나눌 날이 없으니 판정하지 않는다
            single_day.append(team)
            continue
        goal = (sum(per_cell_grad[(team, d)] for d in days) / seated) if seated else 0.0
        if not from_roster:
            goal = target
        low, high = goal - tolerance, goal + tolerance
        days_out: list[str] = []
        ratios: dict[str, float] = {}
        for day in days:
            ratio = per_cell_grad[(team, day)] / per_cell_total[(team, day)]
            ratios[day] = round(ratio, 4)
            evaluated += 1
            if not (low - 1e-9 <= ratio <= high + 1e-9):
                days_out.append(day)
                outside.append({"team": team, "day": day, "ratio": round(ratio, 4),
                                "target": round(goal, 4)})
        team_ratios[team] = {
            "target": round(goal, 4),
            "ratios": ratios,
            "outside": days_out,
            "seated": seated,
        }

    base = {
        # 목표를 어디서 가져왔는지 — 화면이 "이 팀 명단이 3.5할이라 그 언저리로
        # 봅니다" 라고 말할 수 있어야 사람이 점수를 믿는다.
        "target_source": "명단" if from_roster else "지정",
        "tolerance": tolerance,
        # 회차 전체의 날별 비율 — 채점하지는 않는다. 화면에 함께 적어 두는 것은
        # 사람이 시간표를 볼 때 세로가 아니라 가로(날)로 먼저 보기 때문이다.
        "day_ratios": {d: round(per_day_grad[d] / per_day_total[d], 4)
                       for d in DAYS if per_day_total[d]},
        "round_ratio": round(round_ratio, 4),
        "single_day_teams": single_day,
    }
    if evaluated == 0:
        # 팀마다 면접일이 하루뿐인 회차 — 나눌 것이 없었으므로 감점하지 않는다
        return 100.0, {**base, "teams": team_ratios, "outside": [],
                       "evaluated": 0, "target": round(target if not from_roster else round_ratio, 4)}

    score = 100.0 * (evaluated - len(outside)) / evaluated
    detail = {
        **base,
        "teams": team_ratios,
        "outside": outside,
        "evaluated": evaluated,
        # 팀마다 목표가 다르므로 대표값 하나는 참고용이다 — 인사가 못 박았으면
        # 그 숫자, 아니면 회차 전체의 비율.
        "target": round(target if not from_roster else round_ratio, 4),
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

    `grad_ratio_target` 도 마찬가지다. 안 주면 **팀마다 그 팀 명단의 실제 비율**
    로 규칙1을 잰다 — 편중을 재는 것이 목적이지 3할을 맞추는 것이 목적이 아니기
    때문이다. 인사가 숫자를 못 박았다면 만들 때와 검증할 때 같은 숫자를 넘겨야
    한다.
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
