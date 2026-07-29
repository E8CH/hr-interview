"""배포안 API 통합 테스트."""
import pytest

from contracts.events import EventType

BODY = {
    "round_id": "R2026-Q3-01",
    "master_version_id": "vm_abc123",
    "allow_duplicate": True,
    "duplicate_score_threshold": 0.8,
    "created_by": "HR김민지",
}


@pytest.fixture
def created_plan(client):
    response = client.post("/api/v1/distribute/plan", json=BODY)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_create_plan(created_plan):
    assert created_plan["status"] == "draft"
    assert created_plan["total_applicants"] == 88
    assert created_plan["team_counts"] == {
        "AI솔루션팀": 16,
        "로봇응용기술팀": 19,
        "미래혁신팀": 17,
        "배터리기술팀": 16,
        "전극기술팀": 20,
    }
    # 실데이터 467명 전원이 배포 대상(결과P·구분R)이라 정원 88명 밖은 미배정
    assert len(created_plan["unassigned"]) == 467 - 88
    assert created_plan["elapsed_seconds"] < 3.0


def test_get_plan_detail(client, created_plan):
    response = client.get(f"/api/v1/distribute/{created_plan['plan_id']}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data["teams"]) == set(data["team_counts"])
    for rows in data["teams"].values():
        for row in rows:
            assert len(row["tags"]) >= 2
            assert "applicant_id" in row and "score" in row


def test_get_plan_not_found(client):
    response = client.get("/api/v1/distribute/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_FOUND"


def test_approve_flow(client, created_plan, bus):
    plan_id = created_plan["plan_id"]
    response = client.post(f"/api/v1/distribute/{plan_id}/approve", json={"actor": "HR김민지"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "approved"
    assert data["approved_by"] == "HR김민지"
    assert data["approved_at"] is not None

    events = bus.published_of(EventType.DISTRIBUTION_APPROVED)
    assert len(events) == 1
    assert events[0].payload["plan_id"] == plan_id
    assert events[0].producer == "distributor"


def test_approve_twice_conflicts(client, created_plan):
    plan_id = created_plan["plan_id"]
    client.post(f"/api/v1/distribute/{plan_id}/approve", json={"actor": "HR김민지"})
    again = client.post(f"/api/v1/distribute/{plan_id}/approve", json={"actor": "HR김민지"})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "VALIDATION_FAILED"


def test_adjust_moves_applicant(client, created_plan, bus):
    plan_id = created_plan["plan_id"]
    detail = client.get(f"/api/v1/distribute/{plan_id}").json()["data"]
    source_team = "AI솔루션팀"
    row = next(r for r in detail["teams"][source_team] if not r["is_duplicate"])

    response = client.post(
        f"/api/v1/distribute/{plan_id}/adjust",
        json={
            "moves": [
                {
                    "applicant_id": row["applicant_id"],
                    "from": source_team,
                    "to": "미래혁신팀",
                    "reason": "HR 판단",
                }
            ],
            "actor": "HR김민지",
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "adjusted"
    assert data["team_counts"]["AI솔루션팀"] == 15
    assert data["team_counts"]["미래혁신팀"] == 18

    after = client.get(f"/api/v1/distribute/{plan_id}").json()["data"]
    moved = next(
        r for r in after["teams"]["미래혁신팀"] if r["applicant_id"] == row["applicant_id"]
    )
    assert "HR_MANUAL" in moved["tags"]
    assert len(bus.published_of(EventType.DISTRIBUTION_ADJUSTED)) == 1


def test_adjust_unknown_team(client, created_plan):
    response = client.post(
        f"/api/v1/distribute/{created_plan['plan_id']}/adjust",
        json={"moves": [{"applicant_id": "x", "from": "AI솔루션팀", "to": "없는팀"}]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_adjust_unknown_assignment(client, created_plan):
    response = client.post(
        f"/api/v1/distribute/{created_plan['plan_id']}/adjust",
        json={"moves": [{"applicant_id": "9999999", "from": "AI솔루션팀", "to": "미래혁신팀"}]},
    )
    assert response.status_code == 404


def test_adjust_empty_moves(client, created_plan):
    response = client.post(
        f"/api/v1/distribute/{created_plan['plan_id']}/adjust", json={"moves": []}
    )
    assert response.status_code == 400


def test_reject_flow(client, created_plan):
    plan_id = created_plan["plan_id"]
    response = client.post(f"/api/v1/distribute/{plan_id}/reject", json={"reason": "정원 재조정"})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "rejected"

    blocked = client.post(f"/api/v1/distribute/{plan_id}/adjust", json={"moves": []})
    assert blocked.status_code in (400, 409)


def test_reject_after_approve_conflicts(client, created_plan):
    plan_id = created_plan["plan_id"]
    client.post(f"/api/v1/distribute/{plan_id}/approve", json={"actor": "HR김민지"})
    response = client.post(f"/api/v1/distribute/{plan_id}/reject", json={"reason": "취소"})
    assert response.status_code == 409


def test_plan_created_event_published(client, bus):
    client.post("/api/v1/distribute/plan", json=BODY)
    events = bus.published_of(EventType.DISTRIBUTION_PLAN_CREATED)
    assert len(events) == 1
    assert events[0].payload["total_applicants"] == 88
    assert events[0].round_id == "R2026-Q3-01"


def test_validation_error_envelope(client):
    response = client.post("/api/v1/distribute/plan", json={"round_id": "R1"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_metrics_and_health(client, created_plan):
    assert client.get("/healthz").json()["status"] == "ok"
    body = client.get("/metrics").text
    assert "distributor_plans_total 1" in body
    assert "distributor_assignments_total" in body
    assert client.get("/").json()["data"]["service"] == "02-distributor"


def test_openapi_docs_exposed(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/api/v1/distribute/plan" in schema["paths"]
    assert "/api/v1/profiles" in schema["paths"]
    assert "/api/v1/distribute/{plan_id}/export/{team_name}" in schema["paths"]
