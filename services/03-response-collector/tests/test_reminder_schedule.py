"""명세 요구: test_reminder_schedule — 24h/48h/68h 정확한 시각 계산"""
from datetime import datetime, timedelta

import pytest

from app.services.reminder_engine import (
    MAX_LEVEL,
    REMINDER_RULES,
    due_level,
    reminder_due_at,
    reminder_schedule,
    rule_for_level,
)

SENT_AT = datetime(2026, 7, 29, 9, 0, 0)


def test_rules_match_spec():
    assert [(r["level"], r["hours_after_send"]) for r in REMINDER_RULES] == [
        (1, 24),
        (2, 48),
        (3, 68),
    ]
    assert [r["tone"] for r in REMINDER_RULES] == ["정중", "마감강조", "최종알림"]
    assert [r["cc_supervisor"] for r in REMINDER_RULES] == [False, False, True]
    assert MAX_LEVEL == 3


@pytest.mark.parametrize(
    "level,hours",
    [(1, 24), (2, 48), (3, 68)],
)
def test_due_at_exact(level, hours):
    assert reminder_due_at(SENT_AT, level) == SENT_AT + timedelta(hours=hours)


def test_schedule_returns_all_three_levels():
    schedule = reminder_schedule(SENT_AT)
    assert sorted(schedule) == [1, 2, 3]
    assert schedule[1] == datetime(2026, 7, 30, 9, 0)
    assert schedule[2] == datetime(2026, 7, 31, 9, 0)
    assert schedule[3] == datetime(2026, 8, 1, 5, 0)


def test_level3_is_20h_after_level2():
    schedule = reminder_schedule(SENT_AT)
    assert schedule[3] - schedule[2] == timedelta(hours=20)


@pytest.mark.parametrize(
    "elapsed_hours,expected",
    [
        (0, None),
        (23.99, None),
        (24, 1),
        (47.9, 1),
        (48, 2),
        (67.9, 2),
        (68, 3),
        (200, 3),
    ],
)
def test_due_level_boundaries(elapsed_hours, expected):
    now = SENT_AT + timedelta(hours=elapsed_hours)
    assert due_level(now, SENT_AT, last_reminder_level=0) == expected


def test_due_level_skips_already_sent_levels():
    now = SENT_AT + timedelta(hours=50)
    assert due_level(now, SENT_AT, last_reminder_level=2) is None
    assert due_level(now, SENT_AT, last_reminder_level=1) == 2


def test_due_level_collapses_backlog_to_highest():
    """스케줄러가 오래 멈춰 있었어도 1·2·3 을 연달아 쏘지 않고 최고 레벨 1건만."""
    now = SENT_AT + timedelta(hours=100)
    assert due_level(now, SENT_AT, last_reminder_level=0) == 3


def test_five_minute_accuracy_window():
    """명세 성공 기준: 스케줄 정확도 ±5분."""
    due = reminder_due_at(SENT_AT, 1)
    assert due_level(due - timedelta(minutes=5), SENT_AT, 0) is None
    assert due_level(due + timedelta(minutes=5), SENT_AT, 0) == 1


def test_rule_for_unknown_level_raises():
    with pytest.raises(KeyError):
        rule_for_level(4)
