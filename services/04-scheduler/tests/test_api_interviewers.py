"""면접관 관리 API 테스트"""
from __future__ import annotations

from app.infrastructure.contracts import HOURS


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
        "availability": {"월": ["09시", "10시"], "화": list(HOURS)},
    }
    created = client.post("/api/v1/interviewers", json=body)
    assert created.status_code == 201
    assert created.json()["data"]["interviewer_id"] == "IV901"

    fetched = client.get("/api/v1/interviewers/IV901").json()["data"]
    assert fetched["availability"]["월"] == ["09시", "10시"]


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
        json={"availability": {"금": ["14시", "15시"]}, "max_daily": 2},
    )
    data = resp.json()["data"]

    assert resp.status_code == 200
    assert data["availability"] == {"금": ["14시", "15시"]}
    assert data["max_daily"] == 2


def test_update_not_found(client):
    resp = client.put("/api/v1/interviewers/GHOST", json={"max_daily": 3})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_get_not_found(client):
    assert client.get("/api/v1/interviewers/GHOST").status_code == 404


def test_invalid_day_rejected(client):
    resp = client.post(
        "/api/v1/interviewers",
        json={"interviewer_id": "IV904", "team": "전극기술팀", "availability": {"토": ["09시"]}},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


def test_invalid_hour_rejected(client):
    resp = client.post(
        "/api/v1/interviewers",
        json={"interviewer_id": "IV905", "team": "전극기술팀", "availability": {"월": ["07시"]}},
    )
    assert resp.status_code == 400


def test_updated_availability_affects_generation(client):
    """가용성을 바꾸면 이후 생성되는 시간표가 그 제약을 지킨다"""
    client.put(
        "/api/v1/interviewers/IV101",
        json={"availability": {"금": ["16시"]}},
    )
    data = client.post(
        "/api/v1/schedules/generate",
        json={"round_id": "R2026-Q3-01", "plan_id": "avail-test", "algorithm": "v5"},
    ).json()["data"]

    assert data["hard_violations"] == 0

    assignments = client.get(f"/api/v1/schedules/{data['schedule_id']}").json()["data"]["assignments"]
    for a in assignments:
        if a["interviewer_id"] == "IV101":
            assert (a["day"], a["hour"]) == ("금", "16시")

    # 원복 (다른 테스트에 영향 주지 않도록)
    client.put(
        "/api/v1/interviewers/IV101",
        json={"availability": {d: ["14시", "15시", "16시"] for d in ["월", "화", "수", "목", "금"]}},
    )
