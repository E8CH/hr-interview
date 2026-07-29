"""이벤트 계약 재수출 (shared/contracts는 읽기 전용)

`shared/contracts/events.py`를 단일 진실 원천으로 삼는다.
경로가 잡히지 않는 환경(설치형 배포 등)에서는 로컬 폴백 정의를 쓴다.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SHARED_DIR = Path(__file__).resolve().parents[3] / "shared"
if _SHARED_DIR.is_dir() and str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

try:  # pragma: no cover - import 경로 분기
    from contracts.events import EventEnvelope, EventType  # type: ignore
except ImportError:  # pragma: no cover - 폴백
    from datetime import datetime
    from uuid import uuid4

    from pydantic import BaseModel, Field

    class EventEnvelope(BaseModel):  # type: ignore[no-redef]
        event_id: str = Field(default_factory=lambda: str(uuid4()))
        event_type: str
        timestamp: datetime = Field(default_factory=datetime.utcnow)
        round_id: str
        producer: str
        correlation_id: str
        payload: dict

    class EventType:  # type: ignore[no-redef]
        MASTER_REGISTERED = "MASTER_REGISTERED"
        DISTRIBUTION_REGISTERED = "DISTRIBUTION_REGISTERED"
        INTEGRITY_VIOLATED = "INTEGRITY_VIOLATED"
        DISTRIBUTION_PLAN_CREATED = "DISTRIBUTION_PLAN_CREATED"
        DISTRIBUTION_APPROVED = "DISTRIBUTION_APPROVED"
        DISTRIBUTION_ADJUSTED = "DISTRIBUTION_ADJUSTED"
        REQUEST_SENT = "REQUEST_SENT"
        RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
        REMINDER_SENT = "REMINDER_SENT"
        NON_RESPONDER_ESCALATED = "NON_RESPONDER_ESCALATED"
        SCHEDULE_GENERATED = "SCHEDULE_GENERATED"
        SCHEDULE_LOCKED = "SCHEDULE_LOCKED"
        RULE_VIOLATED = "RULE_VIOLATED"
        NOSHOW_REPORTED = "NOSHOW_REPORTED"
        REPAIR_EXECUTED = "REPAIR_EXECUTED"
        PARTICIPANT_DEFERRED = "PARTICIPANT_DEFERRED"
        NOTIFICATION_SENT = "NOTIFICATION_SENT"
        NOTIFICATION_FAILED = "NOTIFICATION_FAILED"


# 00_SHARED_CONTRACT.md 카탈로그 전 18종 — 수집 대상 전체
ALL_EVENT_TYPES: list[str] = [
    EventType.MASTER_REGISTERED,
    EventType.DISTRIBUTION_REGISTERED,
    EventType.INTEGRITY_VIOLATED,
    EventType.DISTRIBUTION_PLAN_CREATED,
    EventType.DISTRIBUTION_APPROVED,
    EventType.DISTRIBUTION_ADJUSTED,
    EventType.REQUEST_SENT,
    EventType.RESPONSE_RECEIVED,
    EventType.REMINDER_SENT,
    EventType.NON_RESPONDER_ESCALATED,
    EventType.SCHEDULE_GENERATED,
    EventType.SCHEDULE_LOCKED,
    EventType.RULE_VIOLATED,
    EventType.NOSHOW_REPORTED,
    EventType.REPAIR_EXECUTED,
    EventType.PARTICIPANT_DEFERRED,
    EventType.NOTIFICATION_SENT,
    EventType.NOTIFICATION_FAILED,
]

# 이벤트 → 발행 서비스 (감사 리포트의 producer 검증용)
EVENT_PRODUCERS: dict[str, str] = {
    EventType.MASTER_REGISTERED: "version-manager",
    EventType.DISTRIBUTION_REGISTERED: "version-manager",
    EventType.INTEGRITY_VIOLATED: "version-manager",
    EventType.DISTRIBUTION_PLAN_CREATED: "distributor",
    EventType.DISTRIBUTION_APPROVED: "distributor",
    EventType.DISTRIBUTION_ADJUSTED: "distributor",
    EventType.REQUEST_SENT: "response-collector",
    EventType.RESPONSE_RECEIVED: "response-collector",
    EventType.REMINDER_SENT: "response-collector",
    EventType.NON_RESPONDER_ESCALATED: "response-collector",
    EventType.SCHEDULE_GENERATED: "scheduler",
    EventType.SCHEDULE_LOCKED: "scheduler",
    EventType.RULE_VIOLATED: "scheduler",
    EventType.NOSHOW_REPORTED: "repair-engine",
    EventType.REPAIR_EXECUTED: "repair-engine",
    EventType.PARTICIPANT_DEFERRED: "repair-engine",
    EventType.NOTIFICATION_SENT: "notification-hub",
    EventType.NOTIFICATION_FAILED: "notification-hub",
}

__all__ = ["EventEnvelope", "EventType", "ALL_EVENT_TYPES", "EVENT_PRODUCERS"]
