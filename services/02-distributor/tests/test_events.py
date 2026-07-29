"""이벤트 발행/구독 테스트 — 봉투 규약 · 타 서비스 수신 가능성."""
import json

import pytest
from contracts.events import EventEnvelope, EventType

from app import events
from app.infrastructure.db import DistributionPlanORM, new_session
from app.infrastructure.event_bus import receive_message

BODY = {"round_id": "R2026-Q3-01", "master_version_id": "vm_abc123"}


def make_envelope(event_type: str, payload: dict, round_id: str = "R2026-Q3-01") -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        round_id=round_id,
        producer="version-manager",
        correlation_id="corr-1",
        payload=payload,
    )


def test_envelope_shape(client, bus):
    client.post("/api/v1/distribute/plan", json=BODY)
    envelope = bus.published_of(EventType.DISTRIBUTION_PLAN_CREATED)[0]
    dumped = json.loads(envelope.model_dump_json())
    assert set(dumped) == {
        "event_id",
        "event_type",
        "timestamp",
        "round_id",
        "producer",
        "correlation_id",
        "payload",
    }
    assert dumped["producer"] == "distributor"
    assert dumped["correlation_id"] == dumped["payload"]["plan_id"]


def test_downstream_services_receive_approved(client, bus):
    """Service 03 · 06 이 채널 구독으로 DISTRIBUTION_APPROVED를 수신할 수 있어야 한다."""
    subscriber = bus.listener(EventType.DISTRIBUTION_APPROVED)
    plan = client.post("/api/v1/distribute/plan", json=BODY).json()["data"]
    client.post(
        f"/api/v1/distribute/{plan['plan_id']}/approve", json={"actor": "HR김민지"}
    )

    message = receive_message(subscriber, timeout=2.0)
    assert message is not None, "구독자가 DISTRIBUTION_APPROVED를 수신하지 못함"
    received = EventEnvelope(**json.loads(message["data"]))
    assert received.event_type == EventType.DISTRIBUTION_APPROVED
    assert received.payload["plan_id"] == plan["plan_id"]
    assert received.payload["approver"] == "HR김민지"
    subscriber.close()


def test_master_registered_triggers_auto_plan(client, bus):
    """MASTER_REGISTERED 구독 → 자동 배포안 생성."""
    events.register_subscribers()
    bus.dispatch(
        make_envelope(
            EventType.MASTER_REGISTERED,
            {
                "version_id": "vm_auto_1",
                "fingerprint": "abc",
                "applicant_count": 467,
                "actor": "HR김민지",
            },
        ).model_dump_json()
    )
    with new_session() as session:
        plans = session.query(DistributionPlanORM).all()
    assert len(plans) == 1
    assert plans[0].master_version_id == "vm_auto_1"
    assert plans[0].round_id == "R2026-Q3-01"


def test_master_registered_without_version_id_is_ignored(client, bus):
    events.register_subscribers()
    bus.dispatch(make_envelope(EventType.MASTER_REGISTERED, {"actor": "HR"}).model_dump_json())
    with new_session() as session:
        assert session.query(DistributionPlanORM).count() == 0


@pytest.fixture(autouse=True)
def clear_halt_state():
    yield
    events.clear_halt("R2026-Q3-01")


def test_integrity_violated_halts_distribution(client, bus):
    events.register_subscribers()
    bus.dispatch(
        make_envelope(
            EventType.INTEGRITY_VIOLATED,
            {"status": "FAIL", "duplicate_count": 5, "undistributed_count": 3, "issues": ["dup"]},
        ).model_dump_json()
    )
    assert events.is_halted("R2026-Q3-01") is True

    response = client.post("/api/v1/distribute/plan", json=BODY)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INTEGRITY_VIOLATION"

    events.clear_halt("R2026-Q3-01")
    assert client.post("/api/v1/distribute/plan", json=BODY).status_code == 201


def test_integrity_halt_blocks_auto_plan(client, bus):
    events.register_subscribers()
    bus.dispatch(
        make_envelope(EventType.INTEGRITY_VIOLATED, {"issues": []}).model_dump_json()
    )
    bus.dispatch(
        make_envelope(EventType.MASTER_REGISTERED, {"version_id": "vm_blocked"}).model_dump_json()
    )
    with new_session() as session:
        assert session.query(DistributionPlanORM).count() == 0


def test_dispatch_accepts_dict(bus):
    seen = []
    bus.subscribe("MASTER_REGISTERED", seen.append)
    bus.dispatch(make_envelope(EventType.MASTER_REGISTERED, {"version_id": "v"}).model_dump(mode="json"))
    assert len(seen) == 1
