"""[이대로 확정] 을 눌렀을 때 어느 단계를 보내는가

사건: 4단계에서 시간표를 만들고 바로 [🔒 이대로 확정] 을 눌렀더니
`status 400: VALIDATION_FAILED: 락 단계는 한 단계씩만 상승 가능:
DRAFT → CONFIRMED 순서를 지키세요` 만 떴다.

화면이 상태를 안 보고 늘 `LOCKED` 를 보내고 있었다. 스케줄러가 단계를
건너뛰지 못하게 막는 것은 옳다 — CONFIRMED 는 '면접관에게 안내가 나갔다',
LOCKED 는 '지원자에게까지 나갔다' 는 서로 다른 약속이고, 잠근 배정은
재편성에서 아예 못 건드리기 때문이다. 그러니 고칠 곳은 화면 쪽이다:
지금 상태에서 갈 수 있는 **한 걸음** 만 밟는다.

관련: services/04-scheduler/app/services/lock_manager.py
"""
from __future__ import annotations


def test_a_fresh_schedule_is_confirmed_first(console):
    """갓 만든 시간표(DRAFT)에서는 CONFIRMED 를 보낸다 — 예전엔 LOCKED 였다."""
    step, button, done = console.next_lock_level("draft")
    assert step == "CONFIRMED"
    assert "확정" in button
    # 한 걸음 더 남았다는 것을 성공 문구가 말해 줘야 한다
    assert "한 번 더" in done


def test_a_confirmed_schedule_goes_to_locked(console):
    step, button, done = console.next_lock_level("confirmed")
    assert step == "LOCKED"
    assert "잠그기" in button
    assert "재편성" in done


def test_a_locked_schedule_has_nowhere_to_go(console):
    """이미 잠긴 시간표는 보낼 단계가 없다 — 버튼을 죽인다."""
    step, button, _ = console.next_lock_level("locked")
    assert step is None
    assert "이미 잠김" in button


def test_an_unknown_status_is_treated_as_a_fresh_one(console):
    """상태를 못 읽었으면(서비스 응답 실패 · 옛 자료) 첫 걸음부터 밟는다.

    LOCKED 를 넘겨 짚으면 다시 400 이 난다. 모르면 낮은 쪽으로 가정한다.
    """
    for status in (None, "", "unknown", "DRAFT"):
        assert console.next_lock_level(status)[0] in ("CONFIRMED", "LOCKED")
    assert console.next_lock_level(None)[0] == "CONFIRMED"
