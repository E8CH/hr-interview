"""이벤트 발행/구독 정의

발행: NOTIFICATION_SENT · NOTIFICATION_FAILED · NOTIFICATION_OPENED
구독: DISTRIBUTION_APPROVED · REQUEST_SENT · REMINDER_SENT ·
      NON_RESPONDER_ESCALATED · SCHEDULE_LOCKED · REPAIR_EXECUTED ·
      PARTICIPANT_DEFERRED · INTEGRITY_VIOLATED

주의 1: NOTIFICATION_OPENED 는 00_SHARED_CONTRACT 카탈로그에 없는 06 로컬 확장
이벤트다(명세 06 이 요구). 봉투(EventEnvelope) 규격은 그대로 지킨다.

주의 2: NON_RESPONDER_ESCALATED(03 발행) 와 PARTICIPANT_DEFERRED(05 발행) 는
명세 06 의 구독 목록에는 없지만 00_SHARED_CONTRACT 이벤트 카탈로그가 06 을
주 구독자로 지정하고 있어 함께 구독한다.
"""
from __future__ import annotations

from typing import Any

import structlog

from app.config import settings
from app.contracts.events import EventType

log = structlog.get_logger(__name__)

# --- 발행 이벤트 ---
NOTIFICATION_SENT = EventType.NOTIFICATION_SENT
NOTIFICATION_FAILED = EventType.NOTIFICATION_FAILED
NOTIFICATION_OPENED = "NOTIFICATION_OPENED"  # 06 로컬 확장

PUBLISHED_EVENTS = (NOTIFICATION_SENT, NOTIFICATION_FAILED, NOTIFICATION_OPENED)

# --- 구독 이벤트 → 기본 템플릿 매핑 ---
SUBSCRIBED_EVENTS = (
    EventType.DISTRIBUTION_APPROVED,
    EventType.REQUEST_SENT,
    EventType.REMINDER_SENT,
    EventType.NON_RESPONDER_ESCALATED,
    EventType.SCHEDULE_LOCKED,
    EventType.REPAIR_EXECUTED,
    EventType.PARTICIPANT_DEFERRED,
    EventType.INTEGRITY_VIOLATED,
)

_RECIPIENT_KEYS = ("recipients", "invitees", "applicants", "interviewers", "targets")


def _as_entry(item: Any) -> dict | None:
    """수신자 항목을 {address, cc, context} 형태로 정규화."""
    if isinstance(item, str):
        return {"address": item, "cc": [], "context": {}}
    if isinstance(item, dict):
        address = item.get("recipient") or item.get("email") or item.get("address")
        if not address:
            return None
        context = dict(item.get("context") or {})
        # context 밖의 스칼라 값도 렌더링 변수로 흡수 (name, day, hour 등)
        for key, value in item.items():
            if key in {"recipient", "email", "address", "cc", "context"}:
                continue
            context.setdefault(key, value)
        return {"address": address, "cc": list(item.get("cc") or []), "context": context}
    return None


def extract_recipients(payload: dict, key: str | None = None) -> list[dict]:
    """이벤트 payload 에서 수신자 목록을 뽑는다."""
    keys = (key,) if key else _RECIPIENT_KEYS
    entries: list[dict] = []
    for candidate in keys:
        raw = payload.get(candidate)
        if not raw:
            continue
        if isinstance(raw, (list, tuple)):
            for item in raw:
                entry = _as_entry(item)
                if entry:
                    entries.append(entry)
        else:
            entry = _as_entry(raw)
            if entry:
                entries.append(entry)
        if entries:
            break
    return entries


def _enqueue_many(
    envelope: dict,
    template_id: str,
    entries: list[dict],
    *,
    channel: str = "email",
    base_context: dict | None = None,
) -> list[str]:
    """수신자별로 큐에 적재. 생성된 notification_id 목록 반환."""
    from app.infrastructure.db import session_scope
    from app.services.dispatcher import enqueue

    if not entries:
        log.info(
            "event_no_recipients",
            event_type=envelope.get("event_type"),
            template_id=template_id,
        )
        return []

    round_id = envelope.get("round_id") or None
    correlation_id = envelope.get("correlation_id") or None
    ids: list[str] = []
    with session_scope() as session:
        for entry in entries:
            context = {"round_id": round_id or "", **(base_context or {}), **entry["context"]}
            try:
                notification = enqueue(
                    session,
                    template_id=template_id,
                    channel=channel,
                    recipient=entry["address"],
                    context=context,
                    cc=entry["cc"],
                    correlation_id=correlation_id,
                    round_id=round_id,
                )
            except Exception as exc:
                log.error(
                    "event_enqueue_failed",
                    event_type=envelope.get("event_type"),
                    template_id=template_id,
                    recipient=entry["address"],
                    error=str(exc),
                )
                continue
            ids.append(notification.notification_id)
    log.info(
        "event_triggered_notifications",
        event_type=envelope.get("event_type"),
        template_id=template_id,
        count=len(ids),
    )
    return ids


# --- 구독 핸들러 ---
def on_distribution_approved(envelope: dict) -> list[str]:
    """DISTRIBUTION_APPROVED → applicant_invite 대량 발송"""
    payload = envelope.get("payload") or {}
    return _enqueue_many(envelope, "applicant_invite", extract_recipients(payload))


def on_request_sent(envelope: dict) -> list[str]:
    """REQUEST_SENT → invite 발송 (Service 03 위임)"""
    payload = envelope.get("payload") or {}
    base = {
        "deadline": str(payload.get("deadline", "")),
        "form_link": payload.get("form_link", ""),
    }
    return _enqueue_many(
        envelope, "invite", extract_recipients(payload), base_context=base
    )


def on_reminder_sent(envelope: dict) -> list[str]:
    """REMINDER_SENT → reminder_l{level} 실제 발송"""
    payload = envelope.get("payload") or {}
    level = payload.get("level", 1)
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = 1
    level = min(max(level, 1), 3)
    template_id = f"reminder_l{level}"
    channel = payload.get("channel") or "email"
    if channel not in {"email", "slack", "sms"}:
        channel = "email"

    entries = extract_recipients(payload)
    if not entries:
        address = payload.get("email") or payload.get("recipient")
        if address:
            entries = [
                {
                    "address": address,
                    "cc": list(payload.get("cc") or []),
                    "context": {
                        "name": payload.get("name", payload.get("invitee_id", "")),
                    },
                }
            ]
    base = {
        "deadline": str(payload.get("deadline", "")),
        "form_link": payload.get("form_link", ""),
    }
    if level == 3:
        base["supervisor"] = payload.get("supervisor", "상급자")
    return _enqueue_many(
        envelope, template_id, entries, channel=channel, base_context=base
    )


def on_non_responder_escalated(envelope: dict) -> list[str]:
    """NON_RESPONDER_ESCALATED → reminder_l3 최종 알림 (상급자 CC)

    00_SHARED_CONTRACT 카탈로그에서 06 이 주 구독자로 지정된 이벤트.
    """
    payload = envelope.get("payload") or {}
    entries = extract_recipients(payload)
    if not entries:
        address = payload.get("email") or payload.get("recipient")
        if address:
            entries = [
                {
                    "address": address,
                    "cc": list(payload.get("cc") or []),
                    "context": {"name": payload.get("name", payload.get("invitee_id", ""))},
                }
            ]
    supervisor_email = payload.get("supervisor_email")
    if supervisor_email:
        for entry in entries:
            if supervisor_email not in entry["cc"]:
                entry["cc"].append(supervisor_email)
    base = {
        "deadline": str(payload.get("deadline", "")),
        "form_link": payload.get("form_link", ""),
        "supervisor": payload.get("supervisor", "상급자"),
    }
    return _enqueue_many(envelope, "reminder_l3", entries, base_context=base)


def on_participant_deferred(envelope: dict) -> list[str]:
    """PARTICIPANT_DEFERRED → applicant_defer 이월 안내

    00_SHARED_CONTRACT 카탈로그에서 06 이 주 구독자로 지정된 이벤트.
    """
    payload = envelope.get("payload") or {}
    entries = extract_recipients(payload) or extract_recipients(
        payload, "deferred_recipients"
    )
    return _enqueue_many(
        envelope,
        "applicant_defer",
        entries,
        base_context={"next_round": payload.get("next_round", "다음 회차")},
    )


def on_schedule_locked(envelope: dict) -> list[str]:
    """SCHEDULE_LOCKED → interviewer_confirm + applicant_invite"""
    payload = envelope.get("payload") or {}
    ids: list[str] = []
    interviewers = extract_recipients(payload, "interviewers")
    ids += _enqueue_many(
        envelope,
        "interviewer_confirm",
        interviewers,
        base_context={
            "assignment_count": payload.get("assignments_count", ""),
            "schedule_link": payload.get("schedule_link", "HR 포털 참조"),
        },
    )
    applicants = extract_recipients(payload, "applicants")
    ids += _enqueue_many(envelope, "applicant_invite", applicants)
    return ids


def on_repair_executed(envelope: dict) -> list[str]:
    """REPAIR_EXECUTED → applicant_change 또는 applicant_defer"""
    payload = envelope.get("payload") or {}
    ids: list[str] = []

    rebooked = extract_recipients(payload, "rebooked_recipients")
    ids += _enqueue_many(envelope, "applicant_change", rebooked)

    deferred = extract_recipients(payload, "deferred_recipients")
    ids += _enqueue_many(
        envelope,
        "applicant_defer",
        deferred,
        base_context={"next_round": payload.get("next_round", "다음 회차")},
    )

    if not rebooked and not deferred:
        # 수신자 정보가 없으면 plan_type 기준으로 HR 요약 알림만 발송
        ids += _enqueue_many(
            envelope,
            "hr_alert_repair",
            [{"address": settings.hr_alert_email, "cc": [], "context": {}}],
            channel="slack",
            base_context={
                "plan_type": payload.get("plan_type", "N/A"),
                "rebooked": payload.get("rebooked", 0),
                "deferred": payload.get("deferred", 0),
            },
        )
    return ids


def on_integrity_violated(envelope: dict) -> list[str]:
    """INTEGRITY_VIOLATED → hr_alert_integrity HR팀 발송"""
    payload = envelope.get("payload") or {}
    entries = extract_recipients(payload) or [
        {"address": settings.hr_alert_email, "cc": [], "context": {}}
    ]
    return _enqueue_many(
        envelope,
        "hr_alert_integrity",
        entries,
        channel="slack",
        base_context={
            "status": payload.get("status", "VIOLATED"),
            "duplicate_count": payload.get("duplicate_count", 0),
            "undistributed_count": payload.get("undistributed_count", 0),
        },
    )


HANDLERS = {
    EventType.DISTRIBUTION_APPROVED: on_distribution_approved,
    EventType.REQUEST_SENT: on_request_sent,
    EventType.REMINDER_SENT: on_reminder_sent,
    EventType.NON_RESPONDER_ESCALATED: on_non_responder_escalated,
    EventType.SCHEDULE_LOCKED: on_schedule_locked,
    EventType.REPAIR_EXECUTED: on_repair_executed,
    EventType.PARTICIPANT_DEFERRED: on_participant_deferred,
    EventType.INTEGRITY_VIOLATED: on_integrity_violated,
}


def register_subscribers(bus) -> None:
    """이벤트 버스에 구독 핸들러를 등록한다."""
    for event_type, handler in HANDLERS.items():
        bus.subscribe(event_type, handler)
