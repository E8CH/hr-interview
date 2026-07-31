"""하드/소프트 제약 검증

하드 제약 (하나라도 걸리면 RULE_VIOLATED 이벤트 발행)
  TEAM_CONFLICT          같은 팀 동시간 중복 (규칙2, HARD)
  INTERVIEWER_CONFLICT   면접관 동시간 중복
  APPLICANT_CONFLICT     지원자 동시간 중복 (두 팀이 같이 보는 사람)
  DAILY_LIMIT_EXCEEDED   면접관 1일 타임 한도 초과 (max_daily / 8타임 상한)
  AVAILABILITY_VIOLATION 응답한 가용 시간대 밖 배정
  UNKNOWN_SLOT           정의되지 않은 요일/시간대

소프트 페널티는 4대 규칙의 SOFT 항목 미달분 + 리더 부하 쏠림으로 계산한다.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from app.services.board import MAX_SLOTS_PER_DAY
from app.services.rule_evaluator import RuleReport, _get
from app.infrastructure.contracts import DAYS, HOURS


def _availability_map(interviewers: Iterable[Any] | None) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for iv in interviewers or []:
        result[_get(iv, "interviewer_id")] = {
            "availability": _get(iv, "availability", {}) or {},
            "max_daily": int(_get(iv, "max_daily", 6) or 6),
            "priority": int(_get(iv, "priority", 2) or 2),
        }
    return result


def check_hard_constraints(
    assignments: Iterable[Any], interviewers: Iterable[Any] | None = None
) -> list[dict]:
    items = list(assignments)
    ivs = _availability_map(interviewers)
    violations: list[dict] = []

    team_slots: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    iv_slots: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    # 같이 보는 사람은 자리가 둘이다 — 그 둘이 같은 시각이면 사람이 쪼개져야 한다
    applicant_slots: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    iv_day: Counter = Counter()

    for a in items:
        team = _get(a, "team")
        day = _get(a, "day")
        hour = _get(a, "hour")
        iv_id = _get(a, "interviewer_id")
        applicant_id = _get(a, "applicant_id")

        if day not in DAYS or hour not in HOURS:
            violations.append(
                {
                    "code": "UNKNOWN_SLOT",
                    "day": day,
                    "hour": hour,
                    "applicant_id": applicant_id,
                    "message": f"정의되지 않은 슬롯: {day} {hour}",
                }
            )
            continue

        team_slots[(team, day, hour)].append(applicant_id)
        iv_slots[(iv_id, day, hour)].append(applicant_id)
        applicant_slots[(applicant_id, day, hour)].append(team)
        iv_day[(iv_id, day)] += 1

        meta = ivs.get(iv_id)
        if meta is not None:
            allowed = meta["availability"].get(day, [])
            if allowed and hour not in allowed:
                violations.append(
                    {
                        "code": "AVAILABILITY_VIOLATION",
                        "interviewer_id": iv_id,
                        "day": day,
                        "hour": hour,
                        "applicant_id": applicant_id,
                        "message": f"{iv_id}의 가용 시간대가 아님: {day} {hour}",
                    }
                )
            elif not allowed:
                violations.append(
                    {
                        "code": "AVAILABILITY_VIOLATION",
                        "interviewer_id": iv_id,
                        "day": day,
                        "hour": hour,
                        "applicant_id": applicant_id,
                        "message": f"{iv_id}는 {day}에 가용하지 않음",
                    }
                )

    for (team, day, hour), applicant_ids in sorted(team_slots.items()):
        if len(applicant_ids) > 1:
            violations.append(
                {
                    "code": "TEAM_CONFLICT",
                    "team": team,
                    "day": day,
                    "hour": hour,
                    "applicant_ids": applicant_ids,
                    "message": f"{team} {day} {hour} 동시간 중복 {len(applicant_ids)}건",
                }
            )

    for (iv_id, day, hour), applicant_ids in sorted(iv_slots.items()):
        if len(applicant_ids) > 1:
            violations.append(
                {
                    "code": "INTERVIEWER_CONFLICT",
                    "interviewer_id": iv_id,
                    "day": day,
                    "hour": hour,
                    "applicant_ids": applicant_ids,
                    "message": f"{iv_id} {day} {hour} 중복 배정 {len(applicant_ids)}건",
                }
            )

    for (applicant_id, day, hour), teams in sorted(applicant_slots.items()):
        if len(teams) > 1:
            violations.append(
                {
                    "code": "APPLICANT_CONFLICT",
                    "applicant_id": applicant_id,
                    "day": day,
                    "hour": hour,
                    "teams": teams,
                    "message": f"{applicant_id} {day} {hour} 두 곳 동시 면접 ({', '.join(teams)})",
                }
            )

    for (iv_id, day), count in sorted(iv_day.items()):
        meta = ivs.get(iv_id)
        cap = min(int(meta["max_daily"]) if meta else MAX_SLOTS_PER_DAY, MAX_SLOTS_PER_DAY)
        if count > cap:
            violations.append(
                {
                    "code": "DAILY_LIMIT_EXCEEDED",
                    "interviewer_id": iv_id,
                    "day": day,
                    "count": count,
                    "limit": cap,
                    "message": f"{iv_id} {day} {count}타임 (한도 {cap})",
                }
            )

    return violations


def soft_penalty(
    report: RuleReport, assignments: Iterable[Any], interviewers: Iterable[Any] | None = None
) -> float:
    """SOFT 규칙 미달분 + 리더 부하 쏠림 페널티 (낮을수록 좋음)"""
    items = list(assignments)
    penalty = 0.0
    penalty += (100.0 - report.scores.get("rule1_grad_balance", 100.0)) * 0.10
    penalty += (100.0 - report.scores.get("rule3_vertical_group", 100.0)) * 0.10
    penalty += (100.0 - report.scores.get("rule4_first_slot", 100.0)) * 0.10

    ivs = _availability_map(interviewers)
    if items and ivs:
        leader_load = sum(
            1 for a in items if ivs.get(_get(a, "interviewer_id"), {}).get("priority") == 1
        )
        leader_share = leader_load / len(items)
        # 리더 비중이 절반을 넘어가면 급격히 가중
        penalty += max(0.0, leader_share - 0.5) * 40.0

    return round(penalty, 2)


def validate(
    assignments: Iterable[Any],
    interviewers: Iterable[Any] | None = None,
    report: RuleReport | None = None,
) -> dict:
    items = list(assignments)
    violations = check_hard_constraints(items, interviewers)
    if report is None:
        from app.services.rule_evaluator import rule_compliance

        report = rule_compliance(items, interviewers)
    return {
        "hard_violations": violations,
        "soft_penalty": soft_penalty(report, items, interviewers),
    }
