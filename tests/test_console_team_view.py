"""부서 화면이 '왜 그 자리를 못 맡는지' 를 제대로 말하는가

사건: 한 팀 16명 전원이 "가능하다고 하신 요일 · 시간에는 빈 자리가 없습니다"
로 나왔다. 그 팀 담당자 중에는 **모든 시간이 가능하다고 적어 낸 분**도
있었는데도 그랬다. 까닭은 그 '요일' 이었다 — 우리는 담당자에게 가능한 요일을
물어본 적이 없다. 가능 시간은 앞타임 · 뒤타임 · 모든타임 세 덩어리뿐이고 그
덩어리는 어느 요일에나 똑같이 적용된다. 그런데 저장 형식이 {요일: [칸]} 이라
딸려 들어간 요일이 사람의 뜻인 양 자리를 막았다.

그래서 지금은 화면 어디에도 담당자 가능 요일이 없다. 남은 사유는 둘 뿐이고,
둘은 할 일이 다르다 — 인사에 가능 시간을 받아 달라 / 담당자를 더 넣어라.

한 가지 더: 자동배치가 아무도 못 맡는 자리를 '그날 여유가 가장 많은 분' 에게
한 칸씩 얹어, 두 분이 번갈아 앉는 시간표가 나오던 것도 여기서 막는다.

관련: services/04-scheduler/tests/test_team_days.py (요일을 어떻게 고르는가)
"""
from __future__ import annotations

import pytest

DAYS = ["월", "화", "수", "목", "금"]
HOURS = [f"{i}타임" for i in range(1, 9)]


def _iv(interviewer_id: str, availability: dict) -> dict:
    return {"interviewer_id": interviewer_id, "name": interviewer_id,
            "availability": availability}


def _people(count: int) -> list[dict]:
    return [{"applicant_id": f"A{i:02d}", "name": f"지원자{i:02d}",
             "degree_type": "학사", "order": i} for i in range(count)]


# ------------------------------------------------- 저장된 옛 표기를 읽어 내는가

def test_legacy_hour_names_still_count_as_available(console):
    """'09시' 로 저장된 답도 지금 칸으로 읽는다.

    스케줄러는 읽을 때마다 맞춰 보는데 화면만 날것으로 비교하면, 실제로는
    되는 분이 화면에서만 '되는 칸이 하나도 없는 사람' 이 된다.
    """
    row = _iv("IV1", {"월": ["09시", "10시"]})

    assert console.iv_answered(row) is True
    assert console.iv_availability(row)["월"]          # 앞타임 칸으로 옮겨졌다
    assert console.iv_open_slots(row, ["월"], 8)[0]    # 맡을 자리가 생긴다


# --------------------------------------------------- 요일을 안 가리는가

def test_a_day_written_down_does_not_narrow_anything(console):
    """'금' 에만 적어 낸 분도 월요일 자리를 맡는다.

    화면이 저장된 요일을 그대로 믿던 시절에는 이분이 월 · 화 · 수가 면접
    요일인 팀에서 '되는 칸이 하나도 없는 사람' 이 됐다.
    """
    row = _iv("IV1", {"금": HOURS})

    assert console.iv_availability(row) == {day: HOURS for day in DAYS}
    assert console.iv_open_slots(row, ["월", "화", "수"], 8) == {
        0: set(range(8)), 1: set(range(8)), 2: set(range(8))}


def test_every_day_gets_the_same_slots(console):
    """어느 일차든 맡을 수 있는 칸이 같다 — 덩어리는 요일을 안 가린다."""
    row = _iv("IV1", {"월": HOURS[:4]})

    open_slots = console.iv_open_slots(row, ["월", "화", "수", "목"], 8)

    assert list(open_slots) == [0, 1, 2, 3]
    assert all(slots == {0, 1, 2, 3} for slots in open_slots.values())


# ------------------------------------------------------- 사유를 갈라 적는가

@pytest.fixture()
def one_seat(console):
    """자리를 하나도 못 맡는 담당자 한 명 · 지원자 한 명."""
    def make(**kwargs):
        rows = console.pair_schedule(
            _people(1), {"A00": "IV1"}, {"IV1": "실무1 책임"},
            can={"IV1": {index: set() for index in range(3)}},
            days=["월", "화", "수"], balance=False, **kwargs)
        return rows[0]
    return make


def test_unanswered_is_told_apart(one_seat):
    """아직 안 적어 내신 분 — 인사가 가능 시간을 받아 와야 한다."""
    row = one_seat(unanswered={"IV1"})

    assert row["off_band"] is True
    assert row["off_why"] == "가능 시간을 아직 안 적어 내셨습니다"


def test_no_slot_at_all_is_told_apart(one_seat):
    """적어 낸 덩어리에 맡을 칸이 하나도 없는 분 — 요일 얘기는 하지 않는다."""
    row = one_seat()

    assert row["off_band"] is True
    assert row["off_why"] == "적어 내신 가능 시간에는 맡으실 수 있는 칸이 없습니다"
    assert "요일" not in row["off_why"]


def test_full_slots_are_told_apart(console):
    """맡을 칸은 있는데 이미 다 찬 분 — 담당자를 더 넣어야 한다."""
    rows = console.pair_schedule(
        _people(2), {"A00": "IV1", "A01": "IV1"}, {"IV1": "실무1 책임"},
        can={"IV1": {0: {0}, 1: set(), 2: set()}},
        days=["월", "화", "수"], balance=False)
    late = next(row for row in rows if row["off_band"])

    assert late["off_why"] == "적어 내신 가능 시간의 칸이 이미 다 찼습니다"


def test_a_seat_that_fits_has_no_reason_at_all(console):
    """맡을 수 있는 자리에 앉은 사람에게는 사유를 달지 않는다."""
    rows = console.pair_schedule(
        _people(1), {"A00": "IV1"}, {"IV1": "실무1 책임"},
        can={"IV1": {0: {0, 1}, 1: set(), 2: set()}},
        days=["월", "화", "수"], balance=False)

    assert rows[0]["off_band"] is False
    assert rows[0]["off_why"] == ""


# ------------------------------------------- 못 맡는 자리를 연달아 지는가

def test_unservable_seats_are_carried_in_one_block(console):
    """아무도 못 맡는 자리는 한 분이 한도까지 이어 받는다.

    여유만 보고 고르면 두 분이 한 칸씩 번갈아 앉는다 — 어차피 인사팀
    시간표에서 옮겨질 자리 때문에 아무도 연달아 보지 못하게 된다.
    """
    queue = [f"A{i:02d}" for i in range(8)]

    picks, gaps = console.assign_by_availability(
        queue, ["리더", "실무1"], {"리더": 5, "실무1": 5},
        can={}, per_day=8, day_count=1, lead="리더")

    seat = [picks[aid] for aid in queue]
    switches = sum(1 for before, after in zip(seat, seat[1:]) if before != after)

    assert switches == 1                       # 다섯 칸 + 세 칸, 두 덩어리
    assert seat[:5] == [seat[0]] * 5
    assert len(gaps) == 8                      # 여덟 칸 모두 '못 맡는 자리'


def test_one_person_who_can_cover_the_day_keeps_the_whole_day(console):
    """하루를 다 감당할 수 있는 분이 있으면 그 하루는 통째로 그분이 본다."""
    queue = [f"A{i:02d}" for i in range(8)]

    picks, _gaps = console.assign_by_availability(
        queue, ["리더", "실무1"], {"리더": 6, "실무1": 8},
        can={}, per_day=8, day_count=1, lead="리더")

    assert len(set(picks.values())) == 1
