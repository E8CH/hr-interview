"""면접관 관리 API 테스트"""
from __future__ import annotations

from app.infrastructure.contracts import DAYS, HOURS

H = HOURS


def test_list_interviewers_seeded(client):
    data = client.get("/api/v1/interviewers").json()["data"]
    assert len(data) == 20
    assert {iv["priority"] for iv in data} == {1, 2}


def test_filter_by_team(client):
    data = client.get("/api/v1/interviewers", params={"team": "AI솔루션팀"}).json()["data"]
    assert len(data) == 4
    assert all(iv["team"] == "AI솔루션팀" for iv in data)


def test_create_and_get_interviewer(client):
    body = {
        "interviewer_id": "IV901",
        "name": "신규 면접관",
        "team": "미래혁신팀",
        "max_daily": 4,
        "priority": 2,
        "email": "iv901@example.com",
        "availability": {"1일차": [H[0], H[1]]},
    }
    created = client.post("/api/v1/interviewers", json=body)
    assert created.status_code == 201
    assert created.json()["data"]["interviewer_id"] == "IV901"

    # 어느 날에 적어 보냈든 그 칸은 모든 날에 똑같이 적용된다 —
    # 담당자 가능 날이라는 것이 우리 모델에 없다.
    fetched = client.get("/api/v1/interviewers/IV901").json()["data"]
    assert fetched["availability"] == {day: [H[0], H[1]] for day in DAYS}


def test_duplicate_create_returns_409(client):
    body = {"interviewer_id": "IV902", "team": "전극기술팀"}
    assert client.post("/api/v1/interviewers", json=body).status_code == 201

    dup = client.post("/api/v1/interviewers", json=body)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "CONFLICT"


def test_update_availability(client):
    client.post("/api/v1/interviewers", json={"interviewer_id": "IV903", "team": "배터리기술팀"})

    resp = client.put(
        "/api/v1/interviewers/IV903",
        json={"availability": {"5일차": [H[3], H[4]]}, "max_daily": 2},
    )
    data = resp.json()["data"]

    assert resp.status_code == 200
    assert data["availability"] == {day: [H[3], H[4]] for day in DAYS}   # 날은 안 가린다
    assert data["max_daily"] == 2


def test_update_not_found(client):
    resp = client.put("/api/v1/interviewers/GHOST", json={"max_daily": 3})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_get_not_found(client):
    assert client.get("/api/v1/interviewers/GHOST").status_code == 404


def test_an_unknown_day_is_not_rejected_but_read_as_every_day(client):
    """달력에 없는 날 이름이 와도 물리지 않는다 — 칸만 살려 모든 날에 편다.

    담당자에게 어느 날 되는지 묻지 않으므로, 자료에 적힌 날은 아무 뜻이 없다.
    읽을 때 날은 지워지고 칸만 남는다 — 물려서 회차를 통째로 못 열게 할 까닭이
    없다.
    """
    resp = client.post(
        "/api/v1/interviewers",
        json={"interviewer_id": "IV904", "team": "전극기술팀", "availability": {"8일차": [H[0]]}},
    )
    assert resp.status_code == 201

    fetched = client.get("/api/v1/interviewers/IV904").json()["data"]
    assert fetched["availability"] == {day: [H[0]] for day in DAYS}


def test_the_day_key_never_changes_which_slot_is_read(client):
    """날 이름이 무엇이든 읽히는 칸은 같다 — 날은 뜻을 갖지 않는다."""
    client.post("/api/v1/interviewers",
                json={"interviewer_id": "IV906", "team": "전극기술팀",
                      "availability": {"아무날": [H[2]]}})

    fetched = client.get("/api/v1/interviewers/IV906").json()["data"]
    assert fetched["availability"] == {day: [H[2]] for day in DAYS}


def test_invalid_hour_rejected(client):
    resp = client.post(
        "/api/v1/interviewers",
        json={"interviewer_id": "IV905", "team": "전극기술팀", "availability": {"1일차": ["07시"]}},
    )
    assert resp.status_code == 400


def test_updated_availability_affects_generation(client):
    """가용성을 바꾸면 이후 생성되는 시간표가 그 제약을 지킨다.

    지키는 것은 **칸**이다. 어느 날에 적어 보냈든 날 제약은 남지 않으므로
    IV101 은 어느 날에나 앉을 수 있고, 다만 6타임 말고 다른 칸에는 못 앉는다.
    """
    client.put(
        "/api/v1/interviewers/IV101",
        json={"availability": {"5일차": [H[5]]}},
    )
    data = client.post(
        "/api/v1/schedules/generate",
        json={"round_id": "R2026-Q3-01", "plan_id": "avail-test", "algorithm": "v5"},
    ).json()["data"]

    assert data["hard_violations"] == 0

    assignments = client.get(f"/api/v1/schedules/{data['schedule_id']}").json()["data"]["assignments"]
    for a in assignments:
        if a["interviewer_id"] == "IV101":
            assert a["hour"] == H[5]
            assert a["day"] in DAYS

    # 원복 (다른 테스트에 영향 주지 않도록)
    client.put(
        "/api/v1/interviewers/IV101",
        json={"availability": {d: [H[3], H[4], H[5]] for d in ["1일차", "2일차", "3일차", "4일차", "5일차"]}},
    )
