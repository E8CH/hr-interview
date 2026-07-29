"""알고리즘 레지스트리 — 명세의 v1|v4|v5 및 별칭 처리"""
from __future__ import annotations

from collections.abc import Callable

from app.domain.schemas import ApplicantIn, GenerateConstraints, InterviewerIn, PlanResult
from app.services import algorithm_v1, algorithm_v4, algorithm_v5

Runner = Callable[[list[ApplicantIn], list[InterviewerIn], GenerateConstraints], PlanResult]

ALGORITHMS: dict[str, Runner] = {
    "v1": algorithm_v1.run,
    "v4": algorithm_v4.run,
    "v5": algorithm_v5.run,
}

ALIASES = {
    "v1": "v1",
    "v1_interviewer_first": "v1",
    "interviewer_first": "v1",
    "v4": "v4",
    "v4_hierarchical": "v4",
    "hierarchical": "v4",
    "v5": "v5",
    "v5_integrated": "v5",
    "integrated": "v5",
}


class UnknownAlgorithmError(ValueError):
    pass


def normalize_algorithm(name: str) -> str:
    key = (name or "").strip().lower()
    if key not in ALIASES:
        raise UnknownAlgorithmError(
            f"지원하지 않는 알고리즘: {name} (사용 가능: v1, v4_hierarchical, v5)"
        )
    return ALIASES[key]


def run(
    algorithm: str,
    applicants: list[ApplicantIn],
    interviewers: list[InterviewerIn],
    constraints: GenerateConstraints | None = None,
) -> PlanResult:
    key = normalize_algorithm(algorithm)
    return ALGORITHMS[key](applicants, interviewers, constraints or GenerateConstraints())
