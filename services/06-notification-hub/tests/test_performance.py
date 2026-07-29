"""성능 기준 — 명세 06: 발송 요청 API 200ms 이내"""
from __future__ import annotations

import statistics
import time

SLA_MS = 200.0


def _measure(fn, runs: int = 10) -> list[float]:
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        response = fn()
        elapsed = (time.perf_counter() - start) * 1000
        assert response.status_code == 202, response.text
        samples.append(elapsed)
    return samples


def test_send_responds_within_200ms(client, invite_context):
    payload = {
        "template_id": "invite",
        "channel": "email",
        "recipient": "perf@lge.com",
        "context": invite_context,
    }
    # 첫 호출은 커넥션·템플릿 캐시 워밍업이라 측정에서 제외한다
    assert client.post("/api/v1/notify/send", json=payload).status_code == 202

    samples = _measure(lambda: client.post("/api/v1/notify/send", json=payload))
    median = statistics.median(samples)
    assert median < SLA_MS, f"중앙값 {median:.1f}ms > {SLA_MS}ms ({samples})"


def test_broadcast_50_recipients_within_200ms(client, invite_context):
    payload = {
        "template_id": "invite",
        "channel": "email",
        "recipients": [
            {"recipient": f"perf{i}@lge.com", "context": {"name": f"위원{i}"}}
            for i in range(50)
        ],
        "context": invite_context,
    }
    assert client.post("/api/v1/notify/broadcast", json=payload).status_code == 202

    samples = _measure(
        lambda: client.post("/api/v1/notify/broadcast", json=payload), runs=3
    )
    median = statistics.median(samples)
    assert median < SLA_MS, f"중앙값 {median:.1f}ms > {SLA_MS}ms ({samples})"


def test_history_query_is_indexed(client, invite_context):
    """correlation_id 조회는 인덱스를 타므로 대량 데이터에서도 빠르다."""
    correlation_id = "PERF-CORR-001"
    seeded = client.post(
        "/api/v1/notify/broadcast",
        json={
            "template_id": "invite",
            "channel": "email",
            "correlation_id": correlation_id,
            "recipients": [{"recipient": f"h{i}@lge.com"} for i in range(200)],
            "context": invite_context,
        },
    )
    assert seeded.status_code == 202, seeded.text

    start = time.perf_counter()
    response = client.get(
        "/api/v1/notify/history", params={"correlation_id": correlation_id, "limit": 200}
    )
    elapsed = (time.perf_counter() - start) * 1000

    assert response.status_code == 200
    assert response.json()["data"]["count"] == 200
    assert elapsed < SLA_MS, f"조회 {elapsed:.1f}ms > {SLA_MS}ms"
