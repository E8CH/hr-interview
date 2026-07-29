"""이벤트 발행/구독 — 00_SHARED_CONTRACT의 봉투와 카탈로그를 그대로 사용

발행: SCHEDULE_GENERATED, SCHEDULE_LOCKED, RULE_VIOLATED
구독: RESPONSE_RECEIVED (Service 03), DISTRIBUTION_APPROVED (Service 02)
"""
from __future__ import annotations

import logging
from typing import Any

from app.infrastructure.contracts import (
    EventEnvelope,
    EventType,
    ScheduleGeneratedPayload,
    ScheduleLockedPayload,
)
from app.infrastructure.event_bus import get_event_bus

logger = logging.getLogger(__name__)

# 구독 이벤트로 확보한 상태 (스케줄 생성 트리거 판단용)
READINESS: dict[str, dict[str, Any]] = {}


def _correlation(round_id: str, correlation_id: str | None) -> str:
    return correlation_id or f"corr-{round_id}"


# --------------------------------------------------------------------------
# 발행
# --------------------------------------------------------------------------
def publish_schedule_generated(
    round_id: str,
    schedule_id: str,
    total_assigned: int,
    coverage_pct: float,
    rule_compliance_overall: float,
    correlation_id: str | None = None,
) -> EventEnvelope:
    payload = ScheduleGeneratedPayload(
        schedule_id=schedule_id,
        total_assigned=total_assigned,
        coverage_pct=coverage_pct,
        rule_compliance_overall=rule_compliance_overall,
    )
    return get_event_bus().publish(
        EventType.SCHEDULE_GENERATED,
        round_id=round_id,
        payload=payload.model_dump(),
        correlation_id=_correlation(round_id, correlation_id),
    )


def publish_schedule_locked(
    round_id: str,
    schedule_id: str,
    lock_level: str,
    assignments_count: int,
    correlation_id: str | None = None,
) -> EventEnvelope:
    payload = ScheduleLockedPayload(
        schedule_id=schedule_id,
        lock_level=lock_level,
        assignments_count=assignments_count,
    )
    return get_event_bus().publish(
        EventType.SCHEDULE_LOCKED,
        round_id=round_id,
        payload=payload.model_dump(),
        correlation_id=_correlation(round_id, correlation_id),
    )


def publish_rule_violated(
    round_id: str,
    schedule_id: str,
    violations: list[dict],
    correlation_id: str | None = None,
) -> EventEnvelope:
    return get_event_bus().publish(
        EventType.RULE_VIOLATED,
        round_id=round_id,
        payload={
            "schedule_id": schedule_id,
            "violation_count": len(violations),
            "violations": violations[:50],
        },
        correlation_id=_correlation(round_id, correlation_id),
    )


# --------------------------------------------------------------------------
# 구독
# --------------------------------------------------------------------------
def _mark(round_id: str, key: str, value: Any) -> dict:
    state = READINESS.setdefault(
        round_id, {"responses": 0, "distribution_approved": False, "plan_id": None}
    )
    if key == "responses":
        state["responses"] += 1
    else:
        state[key] = value
    return state


def on_response_received(envelope: EventEnvelope) -> None:
    """Service 03 — 면접관 가용성 응답 수신. 누적되면 스케줄 생성 트리거 가능."""
    state = _mark(envelope.round_id, "responses", None)
    logger.info(
        "RESPONSE_RECEIVED round=%s invitee=%s total=%s",
        envelope.round_id,
        envelope.payload.get("invitee_id"),
        state["responses"],
    )


def on_distribution_approved(envelope: EventEnvelope) -> None:
    """Service 02 — 지원자 명단 확정."""
    _mark(envelope.round_id, "distribution_approved", True)
    _mark(envelope.round_id, "plan_id", envelope.payload.get("plan_id"))
    logger.info(
        "DISTRIBUTION_APPROVED round=%s plan=%s",
        envelope.round_id,
        envelope.payload.get("plan_id"),
    )


def readiness(round_id: str) -> dict[str, Any]:
    return dict(
        READINESS.get(round_id, {"responses": 0, "distribution_approved": False, "plan_id": None})
    )


def register_subscriptions() -> None:
    bus = get_event_bus()
    bus.subscribe(EventType.RESPONSE_RECEIVED, on_response_received)
    bus.subscribe(EventType.DISTRIBUTION_APPROVED, on_distribution_approved)


def reset_readiness() -> None:  # 테스트 격리용
    READINESS.clear()
