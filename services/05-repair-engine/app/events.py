"""이벤트 발행 · 구독

봉투(envelope)는 `shared/contracts/events.py` 의 EventEnvelope 를 그대로 사용한다.
(공통 계약은 읽기 전용 — 여기서 확장하지 않는다)

발행: REPAIR_EXECUTED · PARTICIPANT_DEFERRED · SLOT_REOPENED
구독: SCHEDULE_LOCKED (Service 04)

NOTE: `SLOT_REOPENED` 는 05 명세에는 있으나 00_SHARED_CONTRACT 의 EventType 상수에는
      없다. 공통 계약을 수정하지 않기 위해 로컬 상수로 정의하고 봉투 규격만 준수한다.
"""
from __future__ import annotations

import logging

from shared.contracts.events import EventEnvelope, EventType

from app.config import PRODUCER
from app.infrastructure.event_bus import event_bus

log = logging.getLogger("repair-engine.events")

# --- 05 로컬 이벤트 타입 ---
SLOT_REOPENED = "SLOT_REOPENED"
NOSHOW_REPORTED = EventType.NOSHOW_REPORTED
REPAIR_EXECUTED = EventType.REPAIR_EXECUTED
PARTICIPANT_DEFERRED = EventType.PARTICIPANT_DEFERRED
SCHEDULE_LOCKED = EventType.SCHEDULE_LOCKED


def _emit(event_type: str, round_id: str, correlation_id: str, payload: dict) -> dict:
    envelope = EventEnvelope(
        event_type=event_type,
        round_id=round_id,
        producer=PRODUCER,
        correlation_id=correlation_id,
        payload=payload,
    )
    body = envelope.model_dump(mode="json")
    event_bus.publish(body)
    return body


def publish_noshow_reported(round_id: str, correlation_id: str,
                            noshow_applicant_ids: list[str], reported_by: str) -> dict:
    return _emit(NOSHOW_REPORTED, round_id, correlation_id, {
        "noshow_applicant_ids": noshow_applicant_ids,
        "reported_by": reported_by,
    })


def publish_repair_executed(round_id: str, correlation_id: str, event_id: str,
                            plan_type: str, rebooked: int, deferred: int,
                            schedule_id: str, selected_by: str) -> dict:
    return _emit(REPAIR_EXECUTED, round_id, correlation_id, {
        "event_id": event_id,
        "plan_type": plan_type,
        "rebooked": rebooked,
        "deferred": deferred,
        "schedule_id": schedule_id,
        "selected_by": selected_by,
    })


def publish_participant_deferred(round_id: str, correlation_id: str, event_id: str,
                                 applicant_ids: list[str], reason: str) -> dict:
    return _emit(PARTICIPANT_DEFERRED, round_id, correlation_id, {
        "event_id": event_id,
        "applicant_ids": applicant_ids,
        "deferred_count": len(applicant_ids),
        "reason": reason,
    })


def publish_slot_reopened(round_id: str, correlation_id: str, event_id: str,
                          slots: list[dict]) -> dict:
    return _emit(SLOT_REOPENED, round_id, correlation_id, {
        "event_id": event_id,
        "slots": slots,
        "slot_count": len(slots),
    })


# --- 구독: SCHEDULE_LOCKED ---
def handle_schedule_locked(envelope: dict) -> None:
    """Service 04 가 시간표를 확정하면 재편성 대상 스냅샷을 미리 적재한다."""
    from app.services import repair_service

    payload = envelope.get("payload", {}) or {}
    schedule_id = payload.get("schedule_id")
    round_id = envelope.get("round_id")
    if not schedule_id or not round_id:
        log.warning("SCHEDULE_LOCKED 이벤트에 schedule_id/round_id 누락")
        return
    repair_service.prefetch_schedule(
        schedule_id=schedule_id,
        round_id=round_id,
        lock_level=payload.get("lock_level", "CONFIRMED"),
    )
    log.info("SCHEDULE_LOCKED 수신 · 스냅샷 적재 schedule_id=%s", schedule_id)


def register_subscribers() -> None:
    event_bus.subscribe(SCHEDULE_LOCKED, handle_schedule_locked)
