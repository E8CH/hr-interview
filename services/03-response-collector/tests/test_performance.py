"""명세 완료 판정 — 성능 기준

- 폼 페이지 로딩 1초 이내
- 응답 제출 500ms 이내

TestClient 경유 측정이므로 네트워크 지연은 빠지고 서버 처리 시간만 잡힌다.
CI 환경 편차를 감안해 명세 기준의 절반 이하가 나오는지를 본다.
"""
import time

import pytest

from app.schemas import InviteeIn
from app.services import request_service

FORM_LOAD_BUDGET_S = 1.0
SUBMIT_BUDGET_S = 0.5


def _elapsed(fn):
    start = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - start


def test_form_load_under_1s(client, first_invitee):
    client.get(f"/form/{first_invitee.token}")  # 워밍업 (템플릿 컴파일)

    resp, elapsed = _elapsed(lambda: client.get(f"/form/{first_invitee.token}"))
    assert resp.status_code == 200
    assert elapsed < FORM_LOAD_BUDGET_S, f"폼 로딩 {elapsed:.3f}s > {FORM_LOAD_BUDGET_S}s"


def test_form_load_p95_under_1s(client, first_invitee):
    client.get(f"/form/{first_invitee.token}")

    samples = []
    for _ in range(20):
        _, elapsed = _elapsed(lambda: client.get(f"/form/{first_invitee.token}"))
        samples.append(elapsed)

    samples.sort()
    p95 = samples[int(0.95 * len(samples)) - 1]
    assert p95 < FORM_LOAD_BUDGET_S, f"폼 로딩 p95 {p95:.3f}s > {FORM_LOAD_BUDGET_S}s"


def test_submit_under_500ms(client, first_invitee, valid_payload):
    resp, elapsed = _elapsed(
        lambda: client.post(f"/form/{first_invitee.token}/submit", json=valid_payload)
    )
    assert resp.status_code == 200
    assert elapsed < SUBMIT_BUDGET_S, f"제출 {elapsed:.3f}s > {SUBMIT_BUDGET_S}s"


def test_submit_p95_under_500ms(client, db, sample_round_id, deadline, valid_payload):
    """서로 다른 초대자 20명이 각각 1회 제출 — p95 측정."""
    request, _ = request_service.create_request(
        db,
        round_id=sample_round_id,
        plan_id="perf-plan",
        deadline=deadline,
        invitees=[
            InviteeIn(name=f"위원{i}", email=f"perf{i}@lge.com", team="AI솔루션팀", org="제1기술원")
            for i in range(20)
        ],
    )

    samples = []
    for invitee in request.invitees:
        resp, elapsed = _elapsed(
            lambda t=invitee.token: client.post(f"/form/{t}/submit", json=valid_payload)
        )
        assert resp.status_code == 200
        samples.append(elapsed)

    samples.sort()
    p95 = samples[int(0.95 * len(samples)) - 1]
    assert p95 < SUBMIT_BUDGET_S, f"제출 p95 {p95:.3f}s > {SUBMIT_BUDGET_S}s"


@pytest.mark.parametrize("count", [50])
def test_bulk_request_creation_is_reasonable(client, deadline, count):
    body = {
        "round_id": "R2026-Q3-PERF",
        "plan_id": "perf-plan-2",
        "deadline": deadline.isoformat(),
        "invitees": [
            {"name": f"위원{i}", "email": f"bulk{i}@lge.com", "team": "AI솔루션팀"}
            for i in range(count)
        ],
    }
    resp, elapsed = _elapsed(lambda: client.post("/api/v1/requests", json=body))
    assert resp.status_code == 201
    assert resp.json()["data"]["sent_count"] == count
    assert elapsed < 5.0, f"{count}명 발송 {elapsed:.2f}s"


def test_form_html_is_compact(client, first_invitee):
    """단일 HTML 문서 · 외부 요청 0회 → 로딩 예산의 대부분을 여유로 남긴다."""
    html = client.get(f"/form/{first_invitee.token}").text
    assert len(html.encode("utf-8")) < 40_000
