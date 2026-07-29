"""운영 엔드포인트 — /healthz · / · /metrics (00_SHARED_CONTRACT §5)"""
from app.events import EventType
from app.services import reminder_service, response_service
from tests.conftest import shift_sent_at


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "03-response-collector"}


def test_root_declares_event_contract(client):
    data = client.get("/").json()["data"]
    assert data["service"] == "03-response-collector"
    assert set(data["publishes"]) == {
        EventType.REQUEST_SENT,
        EventType.RESPONSE_RECEIVED,
        EventType.REMINDER_SENT,
        EventType.NON_RESPONDER_ESCALATED,
    }
    assert data["subscribes"] == [EventType.DISTRIBUTION_APPROVED]
    assert data["port"] == 8003


def test_metrics_prometheus_format(client, db, created_request, valid_payload):
    response_service.submit_response(db, created_request.invitees[0], valid_payload)
    shift_sent_at(db, created_request, 69)
    reminder_service.run_reminder_cycle(db)

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")

    text = resp.text
    assert "respcol_requests_total 1" in text
    assert "respcol_invitees_total 3" in text
    assert "respcol_responses_total 1" in text
    assert "respcol_reminders_total 2" in text
    assert "respcol_escalations_total 2" in text
    assert "respcol_pending_responses 2" in text
    assert f'respcol_events_published_total{{event_type="{EventType.RESPONSE_RECEIVED}"}} 1' in text

    for line in text.splitlines():
        assert line.startswith("#") or len(line.split()) >= 2


def test_metrics_on_empty_db(client):
    text = client.get("/metrics").text
    assert "respcol_requests_total 0" in text
    assert "respcol_pending_responses 0" in text


def test_error_envelope_shape(client):
    body = client.get("/api/v1/requests/nope").json()
    assert body["data"] is None
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"].isupper()


def test_unknown_route_uses_error_envelope(client):
    resp = client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_openapi_available(client):
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "Response Collector"
    assert "/api/v1/requests" in schema["paths"]
    assert "/form/{token}" in schema["paths"]
