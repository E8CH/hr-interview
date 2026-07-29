"""이벤트 발행/구독 정의

봉투(`EventEnvelope`)와 이벤트 타입 상수는 `shared/contracts`(읽기 전용)를 그대로 사용한다.
공통 계약에 payload 모델이 없는 `NON_RESPONDER_ESCALATED` 만 본 서비스에서 확장 정의한다.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from shared.contracts.events import (  # noqa: F401  (재수출)
    EventEnvelope,
    EventType,
    ReminderSentPayload,
    RequestSentPayload,
    ResponseReceivedPayload,
)

PRODUCER = "response-collector"

# 본 서비스가 발행하는 이벤트
PUBLISHED_EVENTS = (
    EventType.REQUEST_SENT,
    EventType.RESPONSE_RECEIVED,
    EventType.REMINDER_SENT,
    EventType.NON_RESPONDER_ESCALATED,
)

# 본 서비스가 구독하는 이벤트
SUBSCRIBED_EVENTS = (EventType.DISTRIBUTION_APPROVED,)


class NonResponderEscalatedPayload(BaseModel):
    """공통 계약 카탈로그에는 있으나 payload 모델이 없어 서비스 내부에서 확장 정의.

    봉투 구조는 변경하지 않는다.
    """

    invitee_id: str
    name: str
    email: str
    org: str | None = None
    team: str
    supervisor_email: str | None = None
    hours_since_sent: float
    escalated_at: datetime


def make_envelope(
    event_type: str,
    round_id: str,
    correlation_id: str,
    payload: BaseModel | dict,
) -> EventEnvelope:
    """공통 봉투 생성."""
    body = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    return EventEnvelope(
        event_type=event_type,
        round_id=round_id,
        producer=PRODUCER,
        correlation_id=correlation_id,
        payload=body,
    )
