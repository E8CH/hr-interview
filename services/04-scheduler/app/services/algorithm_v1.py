"""v1 — 면접관 우선 배치 (커버리지 100% 최우선)

지원자를 우선순위(타겟랩·학점·학위) 순으로 정렬한 뒤 가장 이른 가용 슬롯에
그대로 채워 넣는 최단 적합(earliest-fit) 그리디.

특성: 커버리지 100% · 하드 위반 0 · 그러나 상위 우선순위(대학원 지원자)가
월요일에 몰려 규칙1(요일 분산)이 크게 무너진다. v4/v5의 비교 기준선.
"""
from __future__ import annotations

from app.domain.schemas import ApplicantIn, GenerateConstraints, InterviewerIn, PlanResult
from app.infrastructure.contracts import DAYS, HOURS
from app.services.board import Board

NAME = "v1"


def run(
    applicants: list[ApplicantIn],
    interviewers: list[InterviewerIn],
    constraints: GenerateConstraints | None = None,
) -> PlanResult:
    constraints = constraints or GenerateConstraints()
    board = Board(interviewers, max_daily_default=constraints.max_daily_default)

    ordered = sorted(applicants, key=lambda a: (-a.priority_score, a.applicant_id))
    unassigned: list[ApplicantIn] = []

    for applicant in ordered:
        placed = False
        for day in DAYS:
            for hour in HOURS:
                if board.place(applicant, day, hour, list(applicant.tags or ["PRIMARY_JOB"])):
                    placed = True
                    break
            if placed:
                break
        if not placed:
            unassigned.append(applicant)

    return PlanResult(
        algorithm=NAME,
        assignments=board.assignments,
        unassigned=unassigned,
        notes={"strategy": "earliest-fit greedy", "ordered_by": "priority_score desc"},
    )
