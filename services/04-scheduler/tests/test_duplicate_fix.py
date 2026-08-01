"""중복면접자 시간 겹침만 골라 고친다 — 인사 화면의 '오류 수정하기'

두 팀이 같이 보기로 한 사람의 자리 둘이 같은 시각이면 시간표로 성립하지
않는다. 부서끼리는 서로의 시간표를 볼 수 없어 부서에서는 막을 수 없다.

여기서 지켜야 할 것은 '겹친 사람만 옮긴다' 는 것이다. 겹치지도 않은 사람까지
다시 앉히면, 부서가 확인하고 보낸 시간표가 인사 쪽에서 매번 다른 물건이 된다.
"""
from __future__ import annotations

from app.domain.schemas import InterviewerIn
from app.infrastructure.contracts import BAND_ALL, DAYS, HOURS, band_hours
from app.services import duplicate_fix

BAND = BAND_ALL


def _iv(interviewer_id: str, team: str, hours=None, max_daily=len(HOURS)) -> InterviewerIn:
    return InterviewerIn(
        interviewer_id=interviewer_id, name=interviewer_id, team=team,
        max_daily=max_daily, priority=2,
        availability={day: list(hours or band_hours(BAND)) for day in DAYS},
    )


def _row(applicant_id, team, interviewer_id, day, hour, lock_level="DRAFT") -> dict:
    return {
        "applicant_id": applicant_id, "applicant_name": applicant_id,
        "team": team, "interviewer_id": interviewer_id,
        "day": day, "hour": hour, "lock_level": lock_level,
    }


CREW = [_iv("IV_가", "가팀"), _iv("IV_나", "나팀")]


# ------------------------------------------------------------------ 찾아내는가

def test_same_person_same_time_in_two_teams_is_a_conflict():
    rows = [
        _row("A01", "가팀", "IV_가", "2일차", HOURS[2]),
        _row("A01", "나팀", "IV_나", "2일차", HOURS[2]),
    ]

    found = duplicate_fix.conflicts(rows)

    assert len(found) == 1
    assert found[0]["applicant_id"] == "A01"
    assert found[0]["teams"] == ["가팀", "나팀"]


def test_two_interviews_at_different_times_are_fine():
    """같이 보는 사람 자체는 정상이다 — 시각만 다르면 된다."""
    rows = [
        _row("A01", "가팀", "IV_가", "2일차", HOURS[2]),
        _row("A01", "나팀", "IV_나", "2일차", HOURS[5]),
    ]

    assert duplicate_fix.conflicts(rows) == []


def test_different_people_at_the_same_time_are_fine():
    rows = [
        _row("A01", "가팀", "IV_가", "2일차", HOURS[2]),
        _row("A02", "나팀", "IV_나", "2일차", HOURS[2]),
    ]

    assert duplicate_fix.conflicts(rows) == []


# ------------------------------------------------------------ 그 사람만 옮기는가

def test_only_the_clashing_person_moves():
    """겹치지 않은 사람의 자리는 손대지 않는다."""
    rows = [
        _row("A01", "가팀", "IV_가", "2일차", HOURS[2]),
        _row("A01", "나팀", "IV_나", "2일차", HOURS[2]),
        _row("A02", "가팀", "IV_가", "2일차", HOURS[0]),
        _row("A03", "나팀", "IV_나", "2일차", HOURS[1]),
    ]

    moved, stuck = duplicate_fix.plan_fix(rows, CREW)

    assert stuck == []
    assert [m["applicant_id"] for m in moved] == ["A01"]
    assert {m["team"] for m in moved} == {"나팀"}    # 팀 이름 뒤쪽이 옮긴다


def test_the_moved_seat_keeps_its_team_and_interviewer():
    """시각만 바꾼다 — 부서가 정한 팀 · 담당자는 그대로."""
    rows = [
        _row("A01", "가팀", "IV_가", "2일차", HOURS[2]),
        _row("A01", "나팀", "IV_나", "2일차", HOURS[2]),
    ]

    moved, _ = duplicate_fix.plan_fix(rows, CREW)

    assert moved[0]["interviewer_id"] == "IV_나"
    assert moved[0]["team"] == "나팀"
    assert (moved[0]["day"], moved[0]["hour"]) != ("2일차", HOURS[2])


def test_fixing_actually_clears_the_conflict():
    rows = [
        _row("A01", "가팀", "IV_가", "2일차", HOURS[2]),
        _row("A01", "나팀", "IV_나", "2일차", HOURS[2]),
    ]

    duplicate_fix.plan_fix(rows, CREW)     # rows 는 계획대로 손질된 사본을 만든다

    fixed = [
        _row("A01", "가팀", "IV_가", "2일차", HOURS[2]),
        _row("A01", "나팀", "IV_나", "2일차", HOURS[3]),
    ]
    assert duplicate_fix.conflicts(fixed) == []


def test_it_moves_to_the_nearest_open_slot():
    """멀리 던지지 않고 바로 옆 칸으로 옮긴다."""
    rows = [
        _row("A01", "가팀", "IV_가", "2일차", HOURS[4]),
        _row("A01", "나팀", "IV_나", "2일차", HOURS[4]),
    ]

    moved, _ = duplicate_fix.plan_fix(rows, CREW)

    assert moved[0]["day"] == "2일차"
    assert abs(HOURS.index(moved[0]["hour"]) - 4) == 1


# --------------------------------------------------- 담당자 사정을 보는가

def test_it_does_not_move_outside_the_interviewer_hours():
    """옮길 칸도 담당자가 된다고 한 시간 안에서만 고른다."""
    front = band_hours("앞타임")
    crew = [_iv("IV_가", "가팀"), _iv("IV_나", "나팀", hours=front)]
    rows = [
        _row("A01", "가팀", "IV_가", "2일차", front[0]),
        _row("A01", "나팀", "IV_나", "2일차", front[0]),
    ]

    moved, _ = duplicate_fix.plan_fix(rows, crew)

    assert moved[0]["hour"] in front


def test_a_day_at_the_daily_cap_is_not_offered():
    """하루 한도가 찬 날은 옮길 곳으로 치지 않는다."""
    crew = [_iv("IV_나", "나팀", max_daily=1)]
    space = duplicate_fix._Space([_row("A09", "나팀", "IV_나", "2일차", HOURS[3])], crew)
    row = _row("A01", "나팀", "IV_나", "3일차", HOURS[0])

    assert space.why_not(row, "2일차", HOURS[6]) == "IV_CAP"
    assert space.why_not(row, "3일차", HOURS[6]) is None


def test_a_full_day_pushes_the_move_to_the_next_interview_day():
    """제 날이 꽉 찼으면 그 팀의 다음 면접일로 넘어간다."""
    crew = [_iv("IV_가", "가팀"), _iv("IV_나", "나팀")]
    rows = [_row("A01", "가팀", "IV_가", "2일차", HOURS[2])]
    # 나팀 2일차는 여덟 칸이 모두 찼다 — 그중 한 칸이 겹친 자리다
    rows += [_row(f"B{i:02d}" if i != 2 else "A01", "나팀", "IV_나", "2일차", HOURS[i])
             for i in range(len(HOURS))]

    moved, stuck = duplicate_fix.plan_fix(rows, crew, days_by_team={"나팀": ["2일차", "4일차"]})

    assert stuck == []
    assert [m["applicant_id"] for m in moved] == ["A01"]
    assert moved[0]["day"] == "4일차"


def test_it_stays_inside_the_teams_interview_days():
    """그 팀이 안 보는 날로는 넘어가지 않는다."""
    rows = [
        _row("A01", "가팀", "IV_가", "2일차", HOURS[2]),
        _row("A01", "나팀", "IV_나", "2일차", HOURS[2]),
    ]

    moved, _ = duplicate_fix.plan_fix(rows, CREW, days_by_team={"나팀": ["2일차", "4일차"]})

    assert moved[0]["day"] in ("2일차", "4일차")


def test_no_room_is_reported_not_silently_left():
    """옮길 칸이 없으면 그렇다고 말한다 — 조용히 겹친 채로 두지 않는다."""
    crew = [_iv("IV_가", "가팀"), _iv("IV_나", "나팀", hours=[HOURS[2]])]
    rows = [
        _row("A01", "가팀", "IV_가", "2일차", HOURS[2]),
        _row("A01", "나팀", "IV_나", "2일차", HOURS[2]),
    ]

    moved, stuck = duplicate_fix.plan_fix(rows, crew, days_by_team={"나팀": ["2일차"]})

    assert moved == []
    assert [s["reason"] for s in stuck] == ["NO_ROOM"]
    assert stuck[0]["applicant_id"] == "A01"


# ------------------------------------------------------------------ 확정한 자리

def test_a_locked_seat_is_never_moved():
    """확정해 둔 자리는 그대로 두고 다른 쪽을 옮긴다."""
    rows = [
        _row("A01", "가팀", "IV_가", "2일차", HOURS[2]),
        _row("A01", "나팀", "IV_나", "2일차", HOURS[2], lock_level="LOCKED"),
    ]

    moved, stuck = duplicate_fix.plan_fix(rows, CREW)

    assert stuck == []
    assert moved[0]["team"] == "가팀"


def test_both_locked_says_it_cannot_be_fixed():
    rows = [
        _row("A01", "가팀", "IV_가", "2일차", HOURS[2], lock_level="LOCKED"),
        _row("A01", "나팀", "IV_나", "2일차", HOURS[2], lock_level="LOCKED"),
    ]

    moved, stuck = duplicate_fix.plan_fix(rows, CREW)

    assert moved == []
    assert [s["reason"] for s in stuck] == ["LOCKED"]


# ---------------------------------------------------------------- 안 건드리는가

def test_a_clean_schedule_is_left_alone():
    """겹친 데가 없으면 아무것도 옮기지 않는다."""
    rows = [
        _row("A01", "가팀", "IV_가", "2일차", HOURS[0]),
        _row("A02", "나팀", "IV_나", "2일차", HOURS[1]),
    ]

    assert duplicate_fix.plan_fix(rows, CREW) == ([], [])


# ------------------------------------------------------------------ 화면에서

def _collide(schedule_id: str) -> tuple[str, str, str]:
    """만들어 둔 시간표에 일부러 겹친 자리를 만든다 — 부서에서 오는 모양."""
    from app.domain.assignment import Assignment
    from app.infrastructure.db import SessionLocal
    from sqlalchemy import select

    with SessionLocal() as db:
        rows = list(db.scalars(
            select(Assignment).where(Assignment.schedule_id == schedule_id)
        ))
        first = rows[0]
        other = next(r for r in rows if r.team != first.team)
        other.applicant_id = first.applicant_id
        other.applicant_name = first.applicant_name
        other.day, other.hour = first.day, first.hour
        db.commit()
        return first.applicant_id, first.day, first.hour


def test_endpoint_moves_only_the_clashing_seat(client):
    """'오류 수정하기' 는 겹친 사람만 옮긴다."""
    made = client.post(
        "/api/v1/schedules/generate",
        json={"round_id": "R2026-Q3-01", "plan_id": "plan-dupfix", "algorithm": "v5"},
    ).json()["data"]
    schedule_id = made["schedule_id"]
    before = {
        a["assignment_id"]: (a["day"], a["hour"])
        for a in client.get(f"/api/v1/schedules/{schedule_id}").json()["data"]["assignments"]
    }
    applicant_id, day, hour = _collide(schedule_id)

    resp = client.post(f"/api/v1/schedules/{schedule_id}/fix-duplicates", json={})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert [c["applicant_id"] for c in data["conflicts_before"]] == [applicant_id]
    assert data["conflicts_after"] == []
    assert len(data["moved"]) == 1
    assert (data["moved"][0]["from_day"], data["moved"][0]["from_hour"]) == (day, hour)

    after = client.get(f"/api/v1/schedules/{schedule_id}").json()["data"]["assignments"]
    changed = [a for a in after if before.get(a["assignment_id"]) != (a["day"], a["hour"])]
    assert len(changed) == 1                       # 나머지 자리는 그대로
    assert duplicate_fix.FIX_TAG in changed[0]["reason_tags"]


def test_endpoint_on_a_clean_schedule_changes_nothing(client, generated):
    resp = client.post(
        f"/api/v1/schedules/{generated['schedule_id']}/fix-duplicates", json={}
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["conflicts_before"] == []
    assert data["moved"] == []
    assert data["hard_violations"] == 0


def test_endpoint_not_found(client):
    assert client.post("/api/v1/schedules/nope/fix-duplicates", json={}).status_code == 404
