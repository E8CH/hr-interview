"""응답 수집 — 검증 · 저장 · 패턴 학습 · RESPONSE_RECEIVED 발행"""
from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from app.domain.base import new_uuid
from app.domain.invitee import Invitee
from app.domain.request import STATUS_CLOSED
from app.domain.response import Response
from app.events import EventType, ResponseReceivedPayload, make_envelope
from app.infrastructure.event_bus import get_event_bus
from app.services import pattern_learner
from app.services.validator import normalize_payload, validate_form_response
from app.timeutil import hours_between, utcnow

logger = structlog.get_logger(__name__)


class SubmissionError(Exception):
    """폼 제출 거부 — (code, message)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def get_invitee_by_token(db: Session, token: str) -> Invitee | None:
    return db.query(Invitee).filter(Invitee.token == token).one_or_none()


def mark_opened(db: Session, invitee: Invitee) -> Invitee:
    """폼 최초 열람 시각 기록."""
    if invitee.first_opened_at is None:
        invitee.first_opened_at = utcnow()
        db.commit()
    return invitee


def submit_response(db: Session, invitee: Invitee, payload: dict) -> Response:
    """폼 응답 제출.

    Raises:
        SubmissionError: 마감된 요청 · 중복 제출 · 스키마 검증 실패
    """
    if invitee.request.status == STATUS_CLOSED:
        raise SubmissionError("REQUEST_CLOSED", "마감된 요청입니다. 담당자에게 문의해 주세요.")

    if invitee.response is not None:
        raise SubmissionError("ALREADY_SUBMITTED", "이미 응답을 제출하셨습니다.")

    valid, reason = validate_form_response(payload)
    if not valid:
        logger.warning("response_validation_failed", invitee_id=invitee.invitee_id, reason=reason)
        raise SubmissionError("VALIDATION_FAILED", reason)

    submitted_at = utcnow()
    response = Response(
        response_id=new_uuid(),
        invitee_id=invitee.invitee_id,
        submitted_at=submitted_at,
        payload=normalize_payload(payload),
        validated=True,
    )
    db.add(response)

    # 조직별 응답 패턴 학습
    response_hours = compute_response_hours(invitee, submitted_at)
    if response_hours is not None:
        pattern_learner.record_response(db, invitee.org, response_hours)

    db.commit()
    db.refresh(invitee)

    get_event_bus().publish(
        make_envelope(
            EventType.RESPONSE_RECEIVED,
            round_id=invitee.request.round_id,
            correlation_id=invitee.request.correlation_id,
            payload=ResponseReceivedPayload(
                response_id=response.response_id,
                invitee_id=invitee.invitee_id,
                submitted_at=submitted_at,
            ),
        )
    )
    logger.info(
        "response_received",
        response_id=response.response_id,
        invitee_id=invitee.invitee_id,
        slot_count=response.slot_count,
        response_hours=round(response_hours, 2) if response_hours is not None else None,
    )
    return response


def compute_response_hours(invitee: Invitee, submitted_at=None) -> float | None:
    """발송 → 제출까지 소요시간(시간). 발송 전이면 None."""
    sent_at = invitee.sent_at
    if sent_at is None:
        return None
    if submitted_at is None:
        if invitee.response is None:
            return None
        submitted_at = invitee.response.submitted_at
    return max(hours_between(sent_at, submitted_at), 0.0)
