"""부서 화면이 '왜 그 자리를 못 맡는지' 를 제대로 말하는가

사건: 한 팀 16명 전원이 "가능하다고 하신 요일 · 시간에는 빈 자리가 없습니다"
로 나왔다. 그 팀 담당자 중에는 **모든 시간이 가능하다고 적어 낸 분**도
있었는데도 그랬다. 까닭은 팀 면접 요일이 담당자 사정과 상관없이 인원 수만
보고 정해져서, 그 팀 담당자가 한 분도 못 나오는 요일이 면접 요일이 된 것이다.

그때 화면은 두 가지를 잘못했다.
  ① 사유가 뭉뚱그려져 있었다 — 요일이 어긋난 것과 칸이 모자란 것은 할 일이
     완전히 다르다(인사에 요일을 바꿔 달라 / 담당자를 더 넣어라).
  ② 자동배치가 아무도 못 맡는 자리를 '그날 여유가 가장 많은 분' 에게 한 칸씩
     얹어, 두 분이 번갈아 앉는 시간표가 나왔다 — 연달아 보지 못한다.

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


# --------------------------------------------- 못 나오는 요일을 짚어 내는가

def test_open_days_are_listed_in_week_order(console):
    """담당자가 나올 수 있는 요일만, 월 → 금 차례로."""
    rows = [_iv("IV1", {"금": HOURS, "화": HOURS}), _iv("IV2", {})]

    assert console.iv_open_days(rows) == {"IV1": ["화", "금"], "IV2": []}


def test_gap_days_are_the_ones_nobody_can_come(console):
    """팀 면접 요일 중 아무도 못 나오는 날을 집어 준다."""
    rows = [_iv("IV1", {"목": HOURS}), _iv("IV2", {"금": HOURS})]

    assert console.team_days_gap(rows, ["월", "화", "수"]) == ["월", "화", "수"]
    assert console.team_days_gap(rows, ["수", "목"]) == ["수"]
    assert console.team_days_gap(rows, ["목", "금"]) == []


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


def test_day_mismatch_is_told_apart(one_seat):
    """요일이 어긋난 분 — 인사가 팀 면접 요일을 바꿔야 한다."""
    row = one_seat(open_days={"IV1": ["목", "금"]})

    assert row["off_why"] == ("우리 팀 면접 요일(월 · 화 · 수)에 못 나오십니다 — "
                              "적어 내신 요일은 목 · 금 입니다")


def test_full_slots_are_told_apart(console):
    """요일은 맞는데 칸이 다 찬 분 — 담당자를 더 넣어야 한다."""
    rows = console.pair_schedule(
        _people(2), {"A00": "IV1", "A01": "IV1"}, {"IV1": "실무1 책임"},
        can={"IV1": {0: {0}, 1: set(), 2: set()}},
        days=["월", "화", "수"], open_days={"IV1": ["월"]}, balance=False)
    late = next(row for row in rows if row["off_band"])

    assert late["off_why"] == "월 · 화 · 수 중 나오시는 칸이 이미 다 찼습니다"


def test_a_seat_that_fits_has_no_reason_at_all(console):
    """맡을 수 있는 자리에 앉은 사람에게는 사유를 달지 않는다."""
    rows = console.pair_schedule(
        _people(1), {"A00": "IV1"}, {"IV1": "실무1 책임"},
        can={"IV1": {0: {0, 1}, 1: set(), 2: set()}},
        days=["월", "화", "수"], open_days={"IV1": ["월"]}, balance=False)

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
