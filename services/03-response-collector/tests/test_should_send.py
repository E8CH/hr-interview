"""명세 요구: test_should_send — 이미 회신한 사람에겐 리마인더 안 감"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.reminder_engine import should_send_reminder

SENT_AT = datetime(2026, 7, 29, 9, 0, 0)


def make_invitee(last_level=0, sent_at=SENT_AT):
    return SimpleNamespace(sent_at=sent_at, last_reminder_level=last_level)


@pytest.mark.parametrize("elapsed", [24, 48, 68, 240])
def test_responded_never_gets_reminder(elapsed):
    """핵심 조건: 회신한 사람은 어떤 시점에도 리마인더 대상이 아니다."""
    now = SENT_AT + timedelta(hours=elapsed)
    decision = should_send_reminder(now, make_invitee(), response_received=True)
    assert decision.should_send is False
    assert decision.level is None
    assert decision.reason == "이미 회신함"


def test_responded_wins_over_pending_level():
    """레벨이 도래했더라도 회신 여부가 우선."""
    now = SENT_AT + timedelta(hours=70)
    assert should_send_reminder(now, make_invitee(last_level=2), True).should_send is False
    assert should_send_reminder(now, make_invitee(last_level=2), False).should_send is True


@pytest.mark.parametrize("elapsed,level", [(24, 1), (48, 2), (68, 3)])
def test_non_responder_gets_each_level(elapsed, level):
    now = SENT_AT + timedelta(hours=elapsed)
    decision = should_send_reminder(now, make_invitee(last_level=level - 1), False)
    assert decision.should_send is True
    assert decision.level == level


def test_before_first_due_no_reminder():
    now = SENT_AT + timedelta(hours=10)
    decision = should_send_reminder(now, make_invitee(), False)
    assert decision.should_send is False
    assert "미도래" in decision.reason


def test_no_duplicate_at_same_level():
    now = SENT_AT + timedelta(hours=25)
    decision = should_send_reminder(now, make_invitee(last_level=1), False)
    assert decision.should_send is False


def test_stops_after_max_level():
    now = SENT_AT + timedelta(hours=500)
    decision = should_send_reminder(now, make_invitee(last_level=3), False)
    assert decision.should_send is False
    assert "최대 레벨" in decision.reason


def test_unsent_request_skipped():
    decision = should_send_reminder(SENT_AT, make_invitee(sent_at=None), False)
    assert decision.should_send is False
    assert decision.reason == "미발송 요청"


def test_level3_flags_cc_supervisor():
    now = SENT_AT + timedelta(hours=68)
    decision = should_send_reminder(now, make_invitee(last_level=2), False)
    assert decision.level == 3
    assert decision.cc_supervisor is True


def test_level1_and_2_do_not_cc():
    for elapsed, last in [(24, 0), (48, 1)]:
        decision = should_send_reminder(SENT_AT + timedelta(hours=elapsed), make_invitee(last), False)
        assert decision.cc_supervisor is False


def test_decision_is_truthy_when_sending():
    now = SENT_AT + timedelta(hours=24)
    assert bool(should_send_reminder(now, make_invitee(), False)) is True
    assert bool(should_send_reminder(now, make_invitee(), True)) is False
