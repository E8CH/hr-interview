"""헬스체크 · 메트릭 · 공통 규약 테스트"""
from __future__ import annotations


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root_envelope(client):
    body = client.get("/").json()
    assert body["error"] is None
    assert body["data"]["algorithms"] == ["v1", "v4", "v5"]


def test_metrics_prometheus_format(client, generated):
    text = client.get("/metrics").text
    assert "# TYPE scheduler_schedules_generated_total counter" in text
    assert "scheduler_assignments_total" in text
    assert "scheduler_events_published_total" in text


def test_error_envelope_shape(client):
    body = client.get("/api/v1/schedules/missing").json()
    assert body["data"] is None
    assert set(body["error"]) >= {"code", "message"}
