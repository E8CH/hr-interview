"""이벤트 구독자 — DISTRIBUTION_APPROVED (Service 02) 수신 시 자동 요청 발송"""
from __future__ import annotations

from datetime import timedelta

import structlog

from app.domain.request import Request
from app.events import EventEnvelope, EventType
from app.infrastructure.db import session_scope
from app.infrastructure.event_bus import get_event_bus
from app.services import request_service, roster_client
from app.timeutil import utcnow

logger = structlog.get_logger(__name__)

# 배포 승인 → 회신 마감까지 기본 리드타임 (Level 3 리마인더 68h 이후 여유)
DEFAULT_DEADLINE_HOURS = 72


def on_distribution_approved(envelope: EventEnvelope) -> None:
    """`DISTRIBUTION_APPROVED` 수신 → 면접위원 초대 자동 발송."""
    plan_id = envelope.payload.get("plan_id")
    round_id = envelope.round_id
    if not plan_id:
        logger.error("distribution_approved_missing_plan_id", event_id=envelope.event_id)
        return

    with session_scope() as db:
        existing = (
            db.query(Request)
            .filter(Request.plan_id == plan_id, Request.round_id == round_id)
            .first()
        )
        if existing is not None:
            logger.info("distribution_approved_skipped_duplicate", plan_id=plan_id)
            return

        invitees = roster_client.fetch_invitees(plan_id, round_id)
        if not invitees:
            logger.error("distribution_approved_empty_roster", plan_id=plan_id)
            return

        request, sent_count = request_service.create_request(
            db,
            round_id=round_id,
            plan_id=plan_id,
            deadline=utcnow() + timedelta(hours=DEFAULT_DEADLINE_HOURS),
            invitees=invitees,
            correlation_id=envelope.correlation_id,
        )

    logger.info(
        "distribution_approved_handled",
        plan_id=plan_id,
        request_id=request.request_id,
        sent_count=sent_count,
    )


def register_subscribers() -> None:
    """앱 기동 시 구독 등록."""
    bus = get_event_bus()
    bus.subscribe(EventType.DISTRIBUTION_APPROVED, on_distribution_approved)
    bus.start_listener()
