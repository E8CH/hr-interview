"""리포트 도메인 — 회차 단계 정의 · 응답 모델"""
from __future__ import annotations

from pydantic import BaseModel

from app.events import EventType


class ReportType:
    ROUND_SUMMARY = "round_summary"
    BEFORE_AFTER = "before_after"
    AUDIT = "audit"


# 회차 라이프사이클 5단계 (00_SHARED_CONTRACT.md §9 기준)
#
#   (단계명, 시작 후보 그룹, 종료 후보 그룹, 종료 시각 산정 방식)
#
# 후보는 "그룹의 리스트"다. 앞쪽 그룹부터 보며, 해당 회차에 실제로 존재하는
# 첫 번째 그룹을 채택한다 (없는 이벤트는 건너뛴다).
# 시작 시각은 채택된 그룹의 가장 이른 이벤트.
# 종료 시각 산정:
#   "first" — 다음 단계의 첫 이벤트가 곧 이 단계의 끝 (자료취합)
#   "last"  — 같은 이벤트가 N번 반복되며 마지막이 끝 (회신수집·안내)
PhaseGroups = list[list[str]]

PHASE_DEFINITIONS: list[tuple[str, PhaseGroups, PhaseGroups, str]] = [
    (
        "자료취합",
        [[EventType.MASTER_REGISTERED]],
        [
            [EventType.DISTRIBUTION_REGISTERED],
            [EventType.DISTRIBUTION_PLAN_CREATED],
            [EventType.DISTRIBUTION_APPROVED],
        ],
        "first",
    ),
    (
        "배포",
        [[EventType.DISTRIBUTION_PLAN_CREATED], [EventType.DISTRIBUTION_REGISTERED]],
        [[EventType.DISTRIBUTION_APPROVED]],
        "last",
    ),
    (
        "회신수집",
        [[EventType.REQUEST_SENT], [EventType.DISTRIBUTION_APPROVED]],
        [[EventType.RESPONSE_RECEIVED], [EventType.SCHEDULE_GENERATED]],
        "last",
    ),
    (
        "배치",
        [[EventType.SCHEDULE_GENERATED]],
        [[EventType.SCHEDULE_LOCKED]],
        "last",
    ),
    (
        "안내",
        [[EventType.SCHEDULE_LOCKED]],
        [[EventType.NOTIFICATION_SENT, EventType.NOTIFICATION_FAILED]],
        "last",
    ),
]


class PhaseDuration(BaseModel):
    phase: str
    start: str | None = None
    end: str | None = None
    duration_h: float = 0.0


class RoundReport(BaseModel):
    round_id: str
    duration_hours: float = 0.0
    phases: list[PhaseDuration] = []
    rule_compliance: dict = {}
    noshow_count: int = 0
    repair_events: int = 0


class MetricDelta(BaseModel):
    before: float | None = None
    after: float | None = None
    delta_pct: float | None = None
    delta_pp: float | None = None

    def to_response(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}
