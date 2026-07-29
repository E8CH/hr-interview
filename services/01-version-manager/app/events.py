"""이벤트 발행 — 공통 계약(shared.contracts)의 EventEnvelope/EventType 사용.

발행 이벤트: MASTER_REGISTERED, DISTRIBUTION_REGISTERED, INTEGRITY_VIOLATED
"""
from uuid import uuid4

from shared.contracts.events import (
    DistributionRegisteredPayload,
    EventEnvelope,
    EventType,
    IntegrityViolatedPayload,
    MasterRegisteredPayload,
)

from app.infrastructure.event_bus import get_event_bus

PRODUCER = "version-manager"


def _publish(event_type: str, round_id: str, payload: dict, correlation_id: str | None = None) -> dict:
    envelope = EventEnvelope(
        event_type=event_type,
        round_id=round_id,
        producer=PRODUCER,
        correlation_id=correlation_id or str(uuid4()),
        payload=payload,
    )
    body = envelope.model_dump(mode="json")
    get_event_bus().publish(body)
    return body


def emit_master_registered(round_id: str, version_id: str, fingerprint: str,
                           applicant_count: int, actor: str,
                           correlation_id: str | None = None) -> dict:
    payload = MasterRegisteredPayload(
        version_id=version_id, fingerprint=fingerprint,
        applicant_count=applicant_count, actor=actor,
    ).model_dump()
    return _publish(EventType.MASTER_REGISTERED, round_id, payload, correlation_id)


def emit_distribution_registered(round_id: str, version_id: str, team_name: str,
                                 fingerprint: str, applicant_count: int, actor: str,
                                 correlation_id: str | None = None) -> dict:
    payload = DistributionRegisteredPayload(
        version_id=version_id, team_name=team_name, fingerprint=fingerprint,
        applicant_count=applicant_count, actor=actor,
    ).model_dump()
    return _publish(EventType.DISTRIBUTION_REGISTERED, round_id, payload, correlation_id)


def emit_integrity_violated(round_id: str, status: str, duplicate_count: int,
                            undistributed_count: int, issues: list,
                            correlation_id: str | None = None) -> dict:
    payload = IntegrityViolatedPayload(
        status=status, duplicate_count=duplicate_count,
        undistributed_count=undistributed_count, issues=issues,
    ).model_dump()
    return _publish(EventType.INTEGRITY_VIOLATED, round_id, payload, correlation_id)
