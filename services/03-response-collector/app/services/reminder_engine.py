"""3단계 자동 리마인더 엔진

명세 규칙:
    Level 1 — 발송 후 24h · 정중     · CC 없음
    Level 2 — 발송 후 48h · 마감강조 · CC 없음
    Level 3 — 발송 후 68h · 최종알림 · 상급자 CC + NON_RESPONDER_ESCALATED

판정 함수(`should_send_reminder`)는 DB/IO 에 의존하지 않는 순수 함수다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, NamedTuple

REMINDER_RULES: list[dict[str, Any]] = [
    {"level": 1, "hours_after_send": 24, "tone": "정중", "cc_supervisor": False},
    {"level": 2, "hours_after_send": 48, "tone": "마감강조", "cc_supervisor": False},
    {"level": 3, "hours_after_send": 68, "tone": "최종알림", "cc_supervisor": True},
]

MAX_LEVEL = max(rule["level"] for rule in REMINDER_RULES)
_RULES_BY_LEVEL = {rule["level"]: rule for rule in REMINDER_RULES}


class ReminderDecision(NamedTuple):
    """리마인더 발송 판정 결과."""

    should_send: bool
    level: int | None
    reason: str

    @property
    def cc_supervisor(self) -> bool:
        return bool(self.level) and rule_for_level(self.level)["cc_supervisor"]

    def __bool__(self) -> bool:  # `if decision:` 로도 쓸 수 있게
        return self.should_send


def rule_for_level(level: int) -> dict[str, Any]:
    """레벨에 해당하는 규칙. 없는 레벨이면 KeyError."""
    return _RULES_BY_LEVEL[level]


def reminder_due_at(sent_at: datetime, level: int) -> datetime:
    """해당 레벨 리마인더의 예정 시각."""
    return sent_at + timedelta(hours=rule_for_level(level)["hours_after_send"])


def reminder_schedule(sent_at: datetime) -> dict[int, datetime]:
    """발송 시각 기준 전체 리마인더 스케줄 {level: 예정시각}."""
    return {rule["level"]: reminder_due_at(sent_at, rule["level"]) for rule in REMINDER_RULES}


def due_level(now: datetime, sent_at: datetime, last_reminder_level: int = 0) -> int | None:
    """지금 보내야 할 리마인더 레벨.

    이미 지나간 레벨이 여러 개면 **가장 높은 레벨 하나만** 보낸다
    (스케줄러가 오래 멈춰 있었을 때 1·2·3 을 연달아 쏘지 않도록).
    """
    candidate: int | None = None
    for rule in REMINDER_RULES:
        level = rule["level"]
        if level <= last_reminder_level:
            continue
        if now >= reminder_due_at(sent_at, level):
            candidate = level
    return candidate


def should_send_reminder(
    now: datetime,
    invitee: Any,
    response_received: bool,
) -> ReminderDecision:
    """리마인더 발송 여부 판정 (순수 함수).

    Args:
        now: 현재 시각 (naive UTC)
        invitee: `sent_at`, `last_reminder_level` 속성을 가진 객체
        response_received: 이미 응답했는지 여부

    Returns:
        ReminderDecision(should_send, level, reason)
    """
    if response_received:
        return ReminderDecision(False, None, "이미 회신함")

    sent_at = getattr(invitee, "sent_at", None)
    if sent_at is None:
        return ReminderDecision(False, None, "미발송 요청")

    last_level = getattr(invitee, "last_reminder_level", 0) or 0
    if last_level >= MAX_LEVEL:
        return ReminderDecision(False, None, f"최대 레벨({MAX_LEVEL}) 도달")

    level = due_level(now, sent_at, last_level)
    if level is None:
        next_level = last_level + 1
        due = reminder_due_at(sent_at, next_level)
        return ReminderDecision(False, None, f"Level {next_level} 예정 시각 미도래 ({due.isoformat()})")

    rule = rule_for_level(level)
    return ReminderDecision(True, level, f"Level {level} 도래 ({rule['tone']})")
