"""오전 · 오후 가르기 — 면접 진행 조건을 따라 움직이는지

칸 이름(1타임 … 8타임)만 보면 몇 시인지 알 수 없다. 실제 시각은 인사팀이
팀별 명단 나누기에서 정한 면접 진행 조건(시작 시각 · 면접 분 · 쉬는 분)이
정하고, '오전 첫 타임 · 오후 첫 타임' 도 그에 따라 움직인다. 칸 번호를 굳혀
두면 30분 면접에서 맞던 자리가 1시간 면접에서 엉뚱한 칸이 된다.
"""
from __future__ import annotations

from app.domain.schemas import ApplicantIn
from app.infrastructure.contracts import (HOURS, day_blocks, first_slots,
                                          hour_spans)
from app.services import hierarchical
from app.services.board import Board

HOUR_LONG = {"start": "09:00", "minutes": 60, "rest": 10}


def test_default_timing_afternoon_starts_at_the_seventh_slot():
    """30분 면접 · 5분 휴식이면 7타임(12:30)이 오후의 첫 칸이다."""
    assert day_blocks() == [list(HOURS[:6]), list(HOURS[6:])]
    assert first_slots() == [HOURS[0], HOURS[6]]
    assert hour_spans()[HOURS[6]][0] == 12 * 60 + 30


def test_slot_straddling_noon_stays_in_the_morning():
    """12시에 걸친 칸(기본 조건의 6타임 11:55~12:25)은 오후의 시작이 아니다.

    거기 오는 사람은 아침에 이미 와 있던 사람이라 지각 위험이 오후 첫 칸과
    다르다. 그래서 시작 시각이 12시를 넘긴 칸부터만 오후로 본다.
    """
    start, end = hour_spans()[HOURS[5]]
    assert start < 12 * 60 < end
    assert HOURS[5] in day_blocks()[0]
    assert HOURS[5] not in first_slots()


def test_hour_long_interviews_move_the_afternoon_first_slot():
    """1시간 면접 · 10분 휴식이면 같은 12:30이 4타임이다."""
    assert day_blocks(HOUR_LONG) == [list(HOURS[:3]), list(HOURS[3:])]
    assert first_slots(HOUR_LONG) == [HOURS[0], HOURS[3]]
    assert hour_spans(HOUR_LONG)[HOURS[3]][0] == 12 * 60 + 30


def test_day_that_ends_before_noon_has_one_block():
    """하루가 오전에 끝나면 오후 덩어리는 없다 — 없는 칸을 지키라고 하지 않는다."""
    timing = {"start": "07:00", "minutes": 20, "rest": 0}   # 07:00 ~ 09:40
    assert day_blocks(timing) == [list(HOURS)]
    assert first_slots(timing) == [HOURS[0]]


def test_day_that_starts_after_noon_has_one_block():
    """오후에만 보는 날도 마찬가지 — 첫 칸은 하나뿐이다."""
    timing = {"start": "13:00", "minutes": 30, "rest": 5}
    assert day_blocks(timing) == [list(HOURS)]
    assert first_slots(timing) == [HOURS[0]]


# --------------------------------------------------------------------------
# 배치도 같은 조건을 따라가는지 — 점수만 맞고 자리가 안 맞으면 소용이 없다
# --------------------------------------------------------------------------
def _seat_one_more(interviewers, timing) -> str:
    """앞의 세 칸이 찬 팀에 한 명을 더 앉히고, 그 사람이 앉은 칸을 돌려준다."""
    team = interviewers[0].team
    board = Board(interviewers, ignore_availability=True)
    people = [
        ApplicantIn(applicant_id=f"AP{i}", name=f"지원자{i}", team=team, degree="학사")
        for i in range(4)
    ]
    for applicant, hour in zip(people[:3], HOURS[:3]):
        assert board.place(applicant, "1일차", hour, ["PRIMARY_JOB"]) is not None

    assert hierarchical.place_group(
        board, team, "1일차", people[3:], timing=timing
    ) == []
    return board.assignments[-1].hour


def test_small_group_avoids_whichever_slot_the_timing_calls_afternoon_first(interviewers):
    """소규모 조는 덩어리의 첫 칸을 비껴 앉는다 — 그 칸이 어디인지는 조건이 정한다.

    앞의 세 칸이 이미 찬 상태에서 한 명을 더 앉히면, 30분 면접에서는 4타임
    (10:45, 오전 한복판)이 그대로 다음 자리다. 1시간 면접에서는 같은 4타임이
    12:30 — 오후 첫 칸이므로 한 칸 더 밀어 5타임에 앉힌다.
    """
    assert _seat_one_more(interviewers, None) == HOURS[3]
    assert _seat_one_more(interviewers, HOUR_LONG) == HOURS[4]
