"""GET /api/v1/rounds/{round_id}/availability — 04(스케줄러)가 먹는 입력."""
import pytest

from app.services import response_service
from shared.contracts.constants import HOURS


@pytest.fixture
def with_responses(db, created_request, valid_payload):
    """3명 중 2명 회신 (화 10시·11시 / 수 14시)."""
    for invitee in created_request.invitees[:2]:
        response_service.submit_response(db, invitee, dict(valid_payload))
    return created_request


def test_availability_flips_slots_into_day_buckets(client, with_responses, sample_round_id):
    rows = client.get(f"/api/v1/rounds/{sample_round_id}/availability").json()["data"]

    assert len(rows) == 2, "회신자만 나와야 한다"
    first = rows[0]
    assert first["availability"] == {"화": [HOURS[1], HOURS[2]], "수": [HOURS[3]]}
    assert first["slot_count"] == 3
    assert first["max_daily"] == 6
    assert first["responded"] is True
    assert first["interviewer_id"]


def test_pending_excluded_by_default(client, with_responses, sample_round_id):
    default = client.get(f"/api/v1/rounds/{sample_round_id}/availability").json()["data"]
    included = client.get(
        f"/api/v1/rounds/{sample_round_id}/availability",
        params={"include_pending": True},
    ).json()["data"]

    assert len(default) == 2
    assert len(included) == 3
    pending = [r for r in included if not r["responded"]]
    assert pending[0]["availability"] == {}
    assert pending[0]["slot_count"] == 0


def test_filter_by_team(client, with_responses, sample_round_id):
    rows = client.get(
        f"/api/v1/rounds/{sample_round_id}/availability",
        params={"team": "AI솔루션팀", "include_pending": True},
    ).json()["data"]
    assert {r["team"] for r in rows} == {"AI솔루션팀"}


def test_leader_gets_priority_one(client, db, created_request, valid_payload, sample_round_id):
    """dept_leader_email 이 본인이면 우선 배정 대상(priority=1)."""
    leader = created_request.invitees[0]
    leader.dept_leader_email = leader.email
    db.commit()
    response_service.submit_response(db, leader, dict(valid_payload))

    rows = client.get(f"/api/v1/rounds/{sample_round_id}/availability").json()["data"]
    assert rows[0]["interviewer_id"] == leader.invitee_id
    assert rows[0]["priority"] == 1


def test_empty_round_returns_empty_list(client):
    rows = client.get("/api/v1/rounds/R-없음/availability").json()["data"]
    assert rows == []


def test_summary_counts_by_team(client, with_responses, sample_round_id):
    data = client.get(f"/api/v1/rounds/{sample_round_id}/availability/summary").json()["data"]
    assert data["invited"] == 3
    assert data["responded"] == 2
    assert data["total_slots"] == 6  # 회신 2명 × 3슬롯
    ai = next(t for t in data["teams"] if t["team"] == "AI솔루션팀")
    assert ai == {"team": "AI솔루션팀", "invited": 2, "responded": 2, "slots": 6}
