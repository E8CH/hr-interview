"""GET /form/{token} · POST /form/{token}/submit — 구조화 웹폼"""
import pytest

from app.events import EventType
from shared.contracts.constants import (BAND_ALL, BAND_BACK, BAND_FRONT, DAYS,
                                        HOURS, band_hours)


def test_form_offers_bands_and_never_a_day(client, first_invitee):
    """폼은 시간 덩어리 셋만 묻는다 — 날을 고르게 하지 않는다.

    예전에는 날 × 칸 격자를 그렸는데, 우리 계산에는 담당자 가능 날이라는
    것이 없어서 거기 찍힌 날이 아무 뜻 없이 자리만 막았다.
    """
    resp = client.get(f"/form/{first_invitee.token}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")

    html = resp.text
    assert first_invitee.name in html
    assert html.count('class="slot"') == 3
    for band in (BAND_ALL, BAND_FRONT, BAND_BACK):
        assert f'data-band="{band}"' in html
        assert f'data-hours="{",".join(band_hours(band))}"' in html
    assert "data-day=" not in html          # 날을 고르는 자리가 없다
    assert "data-hour=" not in html         # 칸 하나하나를 고르는 자리도 없다


def test_form_has_no_external_resources(client, first_invitee):
    """외부 CDN 의존 없음 → 로딩 1초 이내 보장."""
    html = client.get(f"/form/{first_invitee.token}").text
    assert "http://" not in html.replace("http://testserver", "")
    assert "https://" not in html
    assert "<script src" not in html
    assert "<link rel=\"stylesheet\"" not in html


def test_form_records_first_opened_at(client, db, first_invitee):
    assert first_invitee.first_opened_at is None
    client.get(f"/form/{first_invitee.token}")

    db.expire_all()
    from app.domain.invitee import Invitee

    refreshed = db.get(Invitee, first_invitee.invitee_id)
    assert refreshed.first_opened_at is not None

    first = refreshed.first_opened_at
    client.get(f"/form/{first_invitee.token}")
    db.expire_all()
    assert db.get(Invitee, first_invitee.invitee_id).first_opened_at == first


def test_invalid_token_404(client):
    resp = client.get("/form/nope-nope-nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_submit_valid_response(client, bus, first_invitee, valid_payload):
    resp = client.post(f"/form/{first_invitee.token}/submit", json=valid_payload)
    assert resp.status_code == 200

    data = resp.json()["data"]
    assert data["validated"] is True
    assert data["slot_count"] == 3
    assert data["response_hours"] is not None

    # 명세 완료 판정: RESPONSE_RECEIVED 이벤트 발행
    events = bus.history(EventType.RESPONSE_RECEIVED)
    assert len(events) == 1
    assert events[0].payload["invitee_id"] == first_invitee.invitee_id
    assert events[0].payload["response_id"] == data["response_id"]


def test_submit_persists_payload(client, db, first_invitee, valid_payload):
    client.post(f"/form/{first_invitee.token}/submit", json=valid_payload)

    from app.domain.response import Response

    stored = db.query(Response).one()
    assert stored.payload["job_role"] == "배터리 소재 연구"
    assert stored.payload["available_slots"] == valid_payload["available_slots"]
    assert stored.payload["notes"] == "8/5 오전 학회 발표"
    assert stored.validated is True


def test_duplicate_submission_rejected(client, first_invitee, valid_payload):
    assert client.post(f"/form/{first_invitee.token}/submit", json=valid_payload).status_code == 200
    dup = client.post(f"/form/{first_invitee.token}/submit", json=valid_payload)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "ALREADY_SUBMITTED"


@pytest.mark.parametrize(
    "payload,expected_code",
    [
        ({"job_role": "직무", "available_slots": []}, 422),
        ({"available_slots": [{"day": "2일차", "hour": HOURS[1]}]}, 422),
        ({"job_role": "직무", "available_slots": [{"day": "8일차", "hour": HOURS[1]}]}, 422),
        ({"job_role": "직무", "available_slots": [{"day": "2일차", "hour": "13시"}]}, 422),
    ],
)
def test_invalid_submission_rejected(client, first_invitee, payload, expected_code):
    resp = client.post(f"/form/{first_invitee.token}/submit", json=payload)
    assert resp.status_code == expected_code
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"
    assert resp.json()["data"] is None


def test_invalid_submission_publishes_no_event(client, bus, first_invitee):
    client.post(f"/form/{first_invitee.token}/submit", json={"job_role": "x", "available_slots": []})
    assert bus.history(EventType.RESPONSE_RECEIVED) == []


def test_submitted_form_is_readonly(client, first_invitee, valid_payload):
    client.post(f"/form/{first_invitee.token}/submit", json=valid_payload)
    html = client.get(f"/form/{first_invitee.token}").text
    assert "이미 응답을 제출하셨습니다" in html
    assert "제출 완료" in html


def test_submit_updates_org_pattern(client, db, first_invitee, valid_payload):
    from app.domain.org_pattern import OrgPattern

    client.post(f"/form/{first_invitee.token}/submit", json=valid_payload)
    row = db.get(OrgPattern, first_invitee.org)
    assert row is not None
    assert row.sample_count == 1
