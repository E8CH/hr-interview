"""옛 규칙으로 저장된 가능 시간을 지금 규칙으로 다시 펼친다

가능 시간은 '앞타임 · 뒤타임 · 둘 다' 라는 덩어리로 받아서 칸 목록으로 펼쳐
저장한다. 저장되는 건 칸 목록뿐이고 덩어리 이름은 남지 않는다. 그래서 덩어리
규칙을 바꿔도 이미 저장된 칸 목록은 옛 규칙 그대로 남는다.

예전에는 정오에 걸치는 칸이 오전에도 오후에도 들어가지 않아, 그 팀 담당자
전원이 그 칸을 못 맡는 일이 생겼다 — 시간표 한가운데가 비는 까닭이었다.
"""
from __future__ import annotations

import pytest

from app.domain.interviewer import Interviewer as InterviewerRow
from app.infrastructure.contracts import BAND_BACK, BAND_FRONT, DAYS, HOURS, band_hours
from app.infrastructure.db import SessionLocal
from app.services import schedule_service


@pytest.fixture
def db(client):
    """client 를 먼저 세워 테이블 · 시작 시 보정이 끝난 뒤의 세션."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _add(db, interviewer_id: str, availability: dict[str, list[str]], max_daily: int):
    db.merge(InterviewerRow(
        interviewer_id=interviewer_id, name=interviewer_id, team="배터리기술팀",
        max_daily=max_daily, priority=2, availability=availability,
    ))
    db.commit()


def _availability(db, interviewer_id: str) -> dict[str, list[str]]:
    return db.get(InterviewerRow, interviewer_id).availability


def test_old_morning_only_row_is_widened(db):
    """오전 = 1~5타임으로 굳어 있던 줄을 지금의 앞타임(하루 전체)로 넓힌다."""
    old_morning = {day: list(HOURS[:5]) for day in DAYS}
    _add(db, "IV_OLD_AM", old_morning, max_daily=3)

    assert schedule_service.rederive_bands(db) >= 1

    db.expire_all()
    widened = _availability(db, "IV_OLD_AM")
    # 지금의 앞타임은 14시까지 — 기본 진행 조건에서는 하루 여덟 칸 전부다
    assert widened == {day: list(band_hours(BAND_FRONT)) for day in DAYS}
    # 정오 언저리 칸이 되살아났는지가 이 보정의 목적이다
    assert HOURS[5] in widened["월"]


def test_daily_cap_is_never_touched(db):
    """맡을 수 있는 칸만 넓히고 하루 몇 명까지인지는 손대지 않는다.

    하루 한도는 사람이 직접 낮춰 둔 숫자일 수 있다. 칸을 넓히는 것과 더 많이
    보게 하는 것은 다른 이야기다.
    """
    _add(db, "IV_OLD_CAP", {day: list(HOURS[:5]) for day in DAYS}, max_daily=2)

    schedule_service.rederive_bands(db)

    db.expire_all()
    assert db.get(InterviewerRow, "IV_OLD_CAP").max_daily == 2


def test_current_back_band_row_is_left_alone(db):
    """이미 지금 규칙으로 저장된 뒤타임은 그대로 둔다 — 다시 돌려도 마찬가지."""
    back = {day: list(band_hours(BAND_BACK)) for day in DAYS}
    _add(db, "IV_BACK_OK", back, max_daily=4)

    schedule_service.rederive_bands(db)

    db.expire_all()
    assert _availability(db, "IV_BACK_OK") == back


def test_second_run_changes_nothing(db):
    """한 번 넓히면 끝이다 — 서비스가 뜰 때마다 도는 보정이라 중요하다."""
    _add(db, "IV_TWICE", {day: list(HOURS[:5]) for day in DAYS}, max_daily=3)

    schedule_service.rederive_bands(db)
    assert schedule_service.rederive_bands(db) == 0


def test_empty_availability_stays_empty(db):
    """'어려움' 으로 비워 둔 사람을 억지로 채워 넣지 않는다."""
    _add(db, "IV_NONE", {}, max_daily=3)

    schedule_service.rederive_bands(db)

    db.expire_all()
    assert _availability(db, "IV_NONE") in ({}, None)
