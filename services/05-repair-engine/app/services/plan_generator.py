"""Plan A/B/C 자동 생성

  A_safe        예비 슬롯 · 팀 일치 · 하드 제약 재검증
  B_defer       노쇼자 전원 다음 회차 이월
  C_cross_team  팀 불일치 허용 (팀 일치를 먼저 시도한 뒤 cross-team)

세 Plan 모두 하드 위반 0 을 만족해야 한다 — 위반이 남는 Plan 은 제시하지 않는다.
"""
from __future__ import annotations

import logging

from app.domain.repair_plan import PLAN_DESCRIPTIONS, RepairPlan
from app.domain.schedule import ScheduleSnapshot
from app.services.safe_repair import RepairOutcome, repair_safely

log = logging.getLogger("repair-engine.plans")

PLAN_SPECS = [
    ("A_safe", {"allow_cross_team": False, "defer_all": False}),
    ("B_defer", {"allow_cross_team": False, "defer_all": True}),
    ("C_cross_team", {"allow_cross_team": True, "defer_all": False}),
]


def _to_plan(event_id: str, plan_type: str, outcome: RepairOutcome) -> RepairPlan:
    warning = None
    if plan_type == "C_cross_team" and outcome.cross_team_count:
        warning = f"Cross-team 사례 {outcome.cross_team_count}건"
    if outcome.hard_violations:
        warning = ((warning + " · ") if warning else "") + \
            f"하드 위반 {outcome.hard_violations}건"

    return RepairPlan(
        event_id=event_id,
        plan_type=plan_type,
        rebooked_count=outcome.rebooked_count,
        deferred_count=outcome.deferred_count,
        hard_violations=outcome.hard_violations,
        soft_penalty=outcome.soft_penalty,
        cross_team_count=outcome.cross_team_count,
        description=PLAN_DESCRIPTIONS[plan_type],
        warning=warning,
        changes=outcome.changes,
        reopened_slots=outcome.reopened_slots,
    )


def generate_plans(event_id: str,
                   snapshot: ScheduleSnapshot,
                   affected_applicant_ids: list[str],
                   lock_overrides: dict[str, str] | None = None,
                   excluded_interviewers: set[str] | None = None) -> list[RepairPlan]:
    plans: list[RepairPlan] = []
    for plan_type, options in PLAN_SPECS:
        outcome = repair_safely(
            snapshot,
            affected_applicant_ids,
            lock_overrides,
            excluded_interviewers=excluded_interviewers,
            **options,
        )
        if outcome.hard_violations:
            # 안전하지 않은 Plan 은 HR 에게 제시하지 않는다.
            log.error("plan %s 하드 위반 %d건 — 폐기: %s",
                      plan_type, outcome.hard_violations, outcome.violation_details)
            continue
        plans.append(_to_plan(event_id, plan_type, outcome))
    return plans
