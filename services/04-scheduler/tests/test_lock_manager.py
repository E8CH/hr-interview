"""3단계 락 시스템 단위 테스트"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services import lock_manager
from app.services.lock_manager import LockTransitionError


@dataclass
class Stub:
    applicant_id: str
    lock_level: str = "DRAFT"


def test_lock_upgrade_follows_draft_confirmed_locked():
    """DRAFT → CONFIRMED → LOCKED 순만 가능"""
    rows = [Stub("1"), Stub("2")]

    lock_manager.apply_lock(rows, "CONFIRMED")
    assert [r.lock_level for r in rows] == ["CONFIRMED", "CONFIRMED"]

    lock_manager.apply_lock(rows, "LOCKED")
    assert [r.lock_level for r in rows] == ["LOCKED", "LOCKED"]


def test_lock_cannot_skip_a_step():
    rows = [Stub("1")]
    with pytest.raises(LockTransitionError):
        lock_manager.apply_lock(rows, "LOCKED")
    assert rows[0].lock_level == "DRAFT"


@pytest.mark.parametrize(
    "current,requested",
    [("CONFIRMED", "DRAFT"), ("LOCKED", "CONFIRMED"), ("LOCKED", "DRAFT")],
)
def test_lock_downgrade_rejected(current, requested):
    rows = [Stub("1", current)]
    with pytest.raises(LockTransitionError):
        lock_manager.apply_lock(rows, requested)
    assert rows[0].lock_level == current


def test_same_level_rejected():
    rows = [Stub("1", "CONFIRMED")]
    with pytest.raises(LockTransitionError):
        lock_manager.apply_lock(rows, "CONFIRMED")


def test_locked_is_terminal():
    assert lock_manager.is_reassignable("DRAFT") is True
    assert lock_manager.is_reassignable("CONFIRMED") is True
    assert lock_manager.is_reassignable("LOCKED") is False


def test_partial_lock_by_applicant_ids():
    rows = [Stub("1"), Stub("2"), Stub("3")]
    changed = lock_manager.apply_lock(rows, "CONFIRMED", applicant_ids=["1", "3"])

    assert {r.applicant_id for r in changed} == {"1", "3"}
    assert rows[1].lock_level == "DRAFT"


def test_partial_lock_is_atomic():
    """대상 중 하나라도 전이 불가면 아무것도 바뀌지 않는다"""
    rows = [Stub("1"), Stub("2", "CONFIRMED")]
    with pytest.raises(LockTransitionError):
        lock_manager.apply_lock(rows, "CONFIRMED", applicant_ids=["1", "2"])
    assert rows[0].lock_level == "DRAFT"


def test_unknown_applicant_rejected():
    with pytest.raises(LockTransitionError):
        lock_manager.apply_lock([Stub("1")], "CONFIRMED", applicant_ids=["999"])


def test_empty_target_rejected():
    with pytest.raises(LockTransitionError):
        lock_manager.apply_lock([], "CONFIRMED")


def test_unknown_level_rejected():
    with pytest.raises(LockTransitionError):
        lock_manager.rank("ARCHIVED")


def test_schedule_status_uses_lowest_level():
    assert lock_manager.schedule_status_for(["LOCKED", "CONFIRMED"]) == "confirmed"
    assert lock_manager.schedule_status_for(["LOCKED", "LOCKED"]) == "locked"
    assert lock_manager.schedule_status_for([]) == "draft"


def test_can_transition_reports_reason():
    decision = lock_manager.can_transition("CONFIRMED", "DRAFT")
    assert decision.allowed is False
    assert "강등" in decision.reason
