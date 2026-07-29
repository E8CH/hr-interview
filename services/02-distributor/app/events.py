"""이벤트 발행 · 구독 (00_SHARED_CONTRACT §4 봉투 준수).

발행: DISTRIBUTION_PLAN_CREATED · DISTRIBUTION_APPROVED · DISTRIBUTION_ADJUSTED
구독: MASTER_REGISTERED (자동 배포안 생성 트리거) · INTEGRITY_VIOLATED (배포 중단)
"""
from __future__ import annotations

import logging

from contracts.events import (
    DistributionApprovedPayload,
    DistributionPlanCreatedPayload,
    EventEnvelope,
    EventType,
)

from app.config import SERVICE_NAME
from app.infrastructure.event_bus import get_bus

logger = logging.getLogger(__name__)

#: INTEGRITY_VIOLATED 수신 시 세팅되는 회차별 배포 중단 플래그
_halted_rounds: set[str] = set()


def _envelope(event_type: str, round_id: str, correlation_id: str, payload: dict) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        round_id=round_id,
        producer=SERVICE_NAME,
        correlation_id=correlation_id,
        payload=payload,
    )


def publish_plan_created(
    round_id: str, plan_id: str, team_counts: dict[str, int], total_applicants: int
) -> EventEnvelope:
    payload = DistributionPlanCreatedPayload(
        plan_id=plan_id, team_counts=team_counts, total_applicants=total_applicants
    )
    return get_bus().publish(
        _envelope(EventType.DISTRIBUTION_PLAN_CREATED, round_id, plan_id, payload.model_dump())
    )


def publish_approved(
    round_id: str, plan_id: str, approver: str, total_applicants: int
) -> EventEnvelope:
    payload = DistributionApprovedPayload(
        plan_id=plan_id, approver=approver, total_applicants=total_applicants
    )
    return get_bus().publish(
        _envelope(EventType.DISTRIBUTION_APPROVED, round_id, plan_id, payload.model_dump())
    )


def publish_adjusted(
    round_id: str, plan_id: str, moves: list[dict], actor: str | None = None
) -> EventEnvelope:
    payload = {"plan_id": plan_id, "moves": moves, "actor": actor, "move_count": len(moves)}
    return get_bus().publish(
        _envelope(EventType.DISTRIBUTION_ADJUSTED, round_id, plan_id, payload)
    )


# --- 구독 ---

def is_halted(round_id: str) -> bool:
    return round_id in _halted_rounds


def clear_halt(round_id: str) -> None:
    _halted_rounds.discard(round_id)


def _on_integrity_violated(envelope: EventEnvelope) -> None:
    _halted_rounds.add(envelope.round_id)
    logger.warning(
        "INTEGRITY_VIOLATED 수신 — 회차 %s 배포 중단 (issues=%s)",
        envelope.round_id,
        envelope.payload.get("issues"),
    )


def _on_master_registered(envelope: EventEnvelope) -> None:
    """(선택) 자동 배포안 생성 트리거.

    순환 import를 피하기 위해 핸들러 내부에서 서비스 계층을 로드한다.
    """
    from app.infrastructure.db import new_session
    from app.services.plan_service import create_plan

    version_id = envelope.payload.get("version_id")
    if not version_id:
        logger.warning("MASTER_REGISTERED payload에 version_id 없음 — 무시")
        return
    if is_halted(envelope.round_id):
        logger.warning("회차 %s 는 무결성 위반으로 중단됨 — 자동 배포 생략", envelope.round_id)
        return
    with new_session() as session:
        summary = create_plan(
            session,
            round_id=envelope.round_id,
            master_version_id=version_id,
            created_by=envelope.payload.get("actor"),
        )
    logger.info("MASTER_REGISTERED → 자동 배포안 생성 plan_id=%s", summary.plan_id)


def register_subscribers() -> None:
    bus = get_bus()
    bus.subscribe(EventType.INTEGRITY_VIOLATED, _on_integrity_violated)
    bus.subscribe(EventType.MASTER_REGISTERED, _on_master_registered)
