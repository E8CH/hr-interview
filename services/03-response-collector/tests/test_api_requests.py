"""POST /api/v1/requests — 초대 요청 발송 API"""
from datetime import datetime, timedelta, timezone

from app.domain.invitee import Invitee
from app.events import EventType


def _body(round_id="R2026-Q3-01", n=3):
    return {
        "round_id": round_id,
        "plan_id": "plan-0001",
        "deadline": (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(),
        "invitees": [
            {
                "name": f"면접위원{i}",
                "email": f"iv{i}@lge.com",
                "team": "AI솔루션팀",
                "org": "제1기술원",
                "dept_leader_email": "lead@lge.com",
            }
            for i in range(n)
        ],
    }


def test_create_request_201(client, bus, notifier):
    resp = client.post("/api/v1/requests", json=_body(n=15))
    assert resp.status_code == 201

    body = resp.json()
    assert body["error"] is None
    assert body["data"]["sent_count"] == 15
    assert body["data"]["request_id"]

    # 명세: REQUEST_SENT 이벤트 발행
    events = bus.history(EventType.REQUEST_SENT)
    assert len(events) == 1
    assert events[0].payload["invitee_count"] == 15
    assert events[0].producer == "response-collector"
    assert events[0].round_id == "R2026-Q3-01"

    # mock 발송 로그 (Service 06 대체)
    outbox = notifier.read_outbox()
    assert len(outbox) == 15
    assert all(m["kind"] == "invitation" for m in outbox)


def test_each_invitee_gets_unique_token(client, db):
    client.post("/api/v1/requests", json=_body(n=5))
    tokens = [i.token for i in db.query(Invitee).all()]
    assert len(tokens) == 5
    assert len(set(tokens)) == 5
    assert all(len(t) >= 32 for t in tokens)


def test_form_link_included_in_invitation(client, db, notifier):
    client.post("/api/v1/requests", json=_body(n=1))
    invitee = db.query(Invitee).one()
    body = notifier.read_outbox()[0]["body"]
    assert f"/form/{invitee.token}" in body


def test_empty_invitees_rejected(client):
    body = _body()
    body["invitees"] = []
    resp = client.post("/api/v1/requests", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


def test_missing_field_rejected(client):
    body = _body()
    del body["round_id"]
    resp = client.post("/api/v1/requests", json=body)
    assert resp.status_code == 422
    assert resp.json()["data"] is None


def test_get_request_detail(client):
    request_id = client.post("/api/v1/requests", json=_body(n=4)).json()["data"]["request_id"]
    resp = client.get(f"/api/v1/requests/{request_id}")
    assert resp.status_code == 200

    data = resp.json()["data"]
    assert data["invitee_count"] == 4
    assert data["responded_count"] == 0
    assert data["status"] == "active"
    assert data["sent_at"] is not None


def test_get_request_not_found(client):
    resp = client.get("/api/v1/requests/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_close_request_blocks_submission(client, db):
    request_id = client.post("/api/v1/requests", json=_body(n=1)).json()["data"]["request_id"]
    assert client.post(f"/api/v1/requests/{request_id}/close").json()["data"]["status"] == "closed"

    token = db.query(Invitee).one().token
    resp = client.post(
        f"/form/{token}/submit",
        json={"job_role": "직무", "available_slots": [{"day": "화", "hour": "10시"}]},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "REQUEST_CLOSED"


def test_team_name_derived_from_majority(client, db):
    from app.domain.request import Request

    body = _body(n=0)
    body["invitees"] = [
        {"name": "A", "email": "a@x.com", "team": "AI솔루션팀"},
        {"name": "B", "email": "b@x.com", "team": "AI솔루션팀"},
        {"name": "C", "email": "c@x.com", "team": "배터리기술팀"},
    ]
    client.post("/api/v1/requests", json=body)
    assert db.query(Request).one().team_name == "AI솔루션팀"
