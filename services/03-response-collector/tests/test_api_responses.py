"""GET /api/v1/responses/{round_id} · /api/v1/patterns/organizations · 리마인더 API"""
import pytest

from app.services import response_service
from tests.conftest import shift_sent_at


@pytest.fixture
def with_responses(db, created_request, valid_payload):
    """3명 중 2명 회신."""
    for invitee in created_request.invitees[:2]:
        response_service.submit_response(db, invitee, dict(valid_payload))
    return created_request


def test_response_summary(client, with_responses, sample_round_id):
    resp = client.get(f"/api/v1/responses/{sample_round_id}")
    assert resp.status_code == 200

    data = resp.json()["data"]
    assert data["total"] == 3
    assert data["responded"] == 2
    assert data["pending"] == 1
    assert data["response_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert data["avg_response_hours"] is not None
    assert len(data["responses"]) == 3


def test_response_items_carry_payload(client, with_responses, sample_round_id):
    items = client.get(f"/api/v1/responses/{sample_round_id}").json()["data"]["responses"]
    responded = [i for i in items if i["responded"]]
    pending = [i for i in items if not i["responded"]]

    assert responded[0]["payload"]["job_role"] == "배터리 소재 연구"
    assert responded[0]["submitted_at"] is not None
    assert pending[0]["payload"] is None
    assert pending[0]["response_hours"] is None


def test_exclude_payload(client, with_responses, sample_round_id):
    items = client.get(
        f"/api/v1/responses/{sample_round_id}", params={"include_payload": False}
    ).json()["data"]["responses"]
    assert all(i["payload"] is None for i in items)


def test_filter_by_team(client, with_responses, sample_round_id):
    data = client.get(
        f"/api/v1/responses/{sample_round_id}", params={"team": "AI솔루션팀"}
    ).json()["data"]
    assert data["total"] == 2
    assert all(i["team"] == "AI솔루션팀" for i in data["responses"])


def test_filter_by_org(client, with_responses, sample_round_id):
    data = client.get(
        f"/api/v1/responses/{sample_round_id}", params={"org": "제1기술원"}
    ).json()["data"]
    assert data["total"] == 2


def test_unknown_round_returns_empty(client):
    data = client.get("/api/v1/responses/R9999-NONE").json()["data"]
    assert data == {
        "round_id": "R9999-NONE",
        "total": 0,
        "responded": 0,
        "pending": 0,
        "response_rate": 0.0,
        "avg_response_hours": None,
        "responses": [],
    }


def test_avg_response_hours_reflects_elapsed_time(client, db, created_request, valid_payload):
    shift_sent_at(db, created_request, 10)
    response_service.submit_response(db, created_request.invitees[0], valid_payload)

    data = client.get(f"/api/v1/responses/{created_request.round_id}").json()["data"]
    assert data["avg_response_hours"] == pytest.approx(10.0, abs=0.1)


# --- 조직 패턴 API ---
def test_org_patterns_endpoint(client, db, created_request, valid_payload):
    shift_sent_at(db, created_request, 6)
    response_service.submit_response(db, created_request.invitees[0], dict(valid_payload))

    shift_sent_at(db, created_request, 52)
    response_service.submit_response(db, created_request.invitees[1], dict(valid_payload))

    data = client.get("/api/v1/patterns/organizations").json()["data"]
    by_org = {row["org"]: row for row in data}

    assert by_org["제1기술원"]["mean_hours"] == pytest.approx(6.0, abs=0.1)
    assert by_org["제1기술원"]["predicted_slow"] is False
    assert by_org["제3기술원"]["mean_hours"] == pytest.approx(52.0, abs=0.1)
    assert by_org["제3기술원"]["predicted_slow"] is True


def test_org_pattern_detail(client, db, created_request, valid_payload):
    response_service.submit_response(db, created_request.invitees[0], valid_payload)
    resp = client.get("/api/v1/patterns/organizations/제1기술원")
    assert resp.status_code == 200
    assert resp.json()["data"]["sample_count"] == 1
    assert resp.json()["data"]["predicted_delay_hours"] is not None


def test_org_pattern_detail_404(client):
    resp = client.get("/api/v1/patterns/organizations/없는조직")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# --- 리마인더 API ---
def test_trigger_reminder_api(client, first_invitee):
    resp = client.post(
        "/api/v1/reminders/trigger", json={"invitee_id": first_invitee.invitee_id, "level": 2}
    )
    assert resp.status_code == 200

    data = resp.json()["data"]
    assert data["sent"] is True
    assert data["level"] == 2
    assert data["cc_supervisor"] is False


def test_trigger_level3_escalates(client, first_invitee):
    data = client.post(
        "/api/v1/reminders/trigger", json={"invitee_id": first_invitee.invitee_id, "level": 3}
    ).json()["data"]
    assert data["cc_supervisor"] is True
    assert data["escalated"] is True


def test_trigger_unknown_invitee_404(client):
    resp = client.post("/api/v1/reminders/trigger", json={"invitee_id": "nope", "level": 1})
    assert resp.status_code == 404


def test_trigger_level_out_of_range(client, first_invitee):
    resp = client.post(
        "/api/v1/reminders/trigger", json={"invitee_id": first_invitee.invitee_id, "level": 9}
    )
    assert resp.status_code == 422


def test_reminder_rules_endpoint(client):
    rules = client.get("/api/v1/reminders/rules").json()["data"]
    assert [r["hours_after_send"] for r in rules] == [24, 48, 68]


def test_reminder_schedule_endpoint(client, first_invitee):
    data = client.get(f"/api/v1/reminders/schedule/{first_invitee.invitee_id}").json()["data"]
    assert [s["level"] for s in data["schedule"]] == [1, 2, 3]
    assert [s["sent"] for s in data["schedule"]] == [False, False, False]
    assert data["schedule"][2]["cc_supervisor"] is True


def test_reminder_schedule_404(client):
    assert client.get("/api/v1/reminders/schedule/nope").status_code == 404


def test_run_cycle_endpoint(client, db, created_request):
    shift_sent_at(db, created_request, 25)
    data = client.post("/api/v1/reminders/run-cycle").json()["data"]
    assert data["sent_count"] == 3
    assert all(s["level"] == 1 for s in data["sent"])
