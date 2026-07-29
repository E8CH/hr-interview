"""3단계 락 시스템 테스트"""
import pytest

from app.domain.repair_event import LOCK_LEVELS, is_immovable, lock_rank
from app.services import lock_service


def test_lock_level_ordering():
    assert LOCK_LEVELS == {"DRAFT": 0, "CONFIRMED": 1, "LOCKED": 2}
    assert lock_rank("DRAFT") < lock_rank("CONFIRMED") < lock_rank("LOCKED")
    assert lock_rank(None) == 0
    assert lock_rank("UNKNOWN") == 0
    assert is_immovable("LOCKED")
    assert not is_immovable("CONFIRMED")


def test_sync_from_snapshot_creates_rows(session, snapshot):
    lock_service.sync_from_snapshot(session, snapshot)
    locks = lock_service.get_locks(session, snapshot.schedule_id)
    assert len(locks) == len(snapshot.assignments)
    assert set(locks.values()) <= set(LOCK_LEVELS)


def test_sync_never_downgrades(session, snapshot):
    lock_service.sync_from_snapshot(session, snapshot)
    target = next(a for a in snapshot.assignments if a.lock_level == "LOCKED")
    lock_service.upgrade_locks(session, snapshot.schedule_id, [target.applicant_id], "LOCKED")

    # 스냅샷 쪽 레벨을 낮춰 다시 동기화해도 LOCKED 유지
    target.lock_level = "DRAFT"
    lock_service.sync_from_snapshot(session, snapshot)
    assert lock_service.get_locks(session, snapshot.schedule_id)[target.applicant_id] == "LOCKED"


def test_upgrade_locks(session, snapshot):
    lock_service.sync_from_snapshot(session, snapshot)
    drafts = [a.applicant_id for a in snapshot.assignments if a.lock_level == "DRAFT"][:3]
    result = lock_service.upgrade_locks(session, snapshot.schedule_id, drafts, "LOCKED")
    assert result["upgraded_count"] == 3
    locks = lock_service.get_locks(session, snapshot.schedule_id)
    assert all(locks[a] == "LOCKED" for a in drafts)


def test_upgrade_rejects_downgrade(session, snapshot):
    lock_service.sync_from_snapshot(session, snapshot)
    locked = [a.applicant_id for a in snapshot.assignments if a.lock_level == "LOCKED"][:2]
    result = lock_service.upgrade_locks(session, snapshot.schedule_id, locked, "DRAFT")
    assert result["upgraded_count"] == 0
    assert len(result["skipped"]) == 2
    locks = lock_service.get_locks(session, snapshot.schedule_id)
    assert all(locks[a] == "LOCKED" for a in locked)


def test_upgrade_unknown_level_raises(session, snapshot):
    with pytest.raises(lock_service.LockError) as exc:
        lock_service.upgrade_locks(session, snapshot.schedule_id, ["X"], "FROZEN")
    assert exc.value.code == "VALIDATION_FAILED"


def test_upgrade_creates_missing_row(session, snapshot):
    result = lock_service.upgrade_locks(session, snapshot.schedule_id, ["9999999"], "CONFIRMED")
    assert result["upgraded"] == ["9999999"]
    assert lock_service.get_locks(session, snapshot.schedule_id)["9999999"] == "CONFIRMED"


def test_list_locks_sorted(session, snapshot):
    lock_service.sync_from_snapshot(session, snapshot)
    rows = lock_service.list_locks(session, snapshot.schedule_id)
    assert rows == sorted(rows, key=lambda r: r["applicant_id"])
    assert rows[0]["updated_at"] is not None
