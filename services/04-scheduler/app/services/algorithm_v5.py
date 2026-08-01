"""v5 — 통합 배치 (커버리지 90%+ 와 규칙 준수 85%+ 동시 달성)

v4 대비 변경점
  1. Stage 1 완화 : 팀당 요일을 2개 → 3개로 늘려 수용량을 16 → 24슬롯으로 확대
                    (5팀 × 24 = 120슬롯)
  2. Stage 3 신규 : Fallback 배치로 미배정자를 흡수. 세로 연속을 깨지 않고
                    요일 대학원 비율을 목표에 가깝게 유지하는 슬롯을 우선 선택.
소프트 페널티를 일부 감수하는 대신 커버리지를 회복한다.
"""
from __future__ import annotations

from app.domain.schemas import ApplicantIn, GenerateConstraints, InterviewerIn, PlanResult
from app.services import algorithm_v4

NAME = "v5"
DAYS_PER_TEAM = 3


def run(
    applicants: list[ApplicantIn],
    interviewers: list[InterviewerIn],
    constraints: GenerateConstraints | None = None,
) -> PlanResult:
    result = algorithm_v4.run(
        applicants,
        interviewers,
        constraints,
        days_per_team=DAYS_PER_TEAM,
        fallback=True,
    )
    result.algorithm = NAME
    result.notes["derived_from"] = "v4 (stage1 relaxed + stage3 fallback)"
    return result
