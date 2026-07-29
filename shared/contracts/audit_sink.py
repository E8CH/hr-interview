"""감사 서비스(Service 07) HTTP 포워더 — 서비스 간 이벤트 전달 보조 경로.

왜 필요한가
    PoC 기본 설정은 `REDIS_URL=fakeredis://` 이고, fakeredis 는 **프로세스 내부
    전용** 인메모리 서버다. start_all_ultra.ps1 은 서비스마다 별도 uvicorn 프로세스를
    띄우므로, 01 이 발행한 이벤트는 07 의 구독 루프에 절대 닿지 않는다.
    (게다가 채널명도 서비스마다 `hr-events` / `hr.events` / `hr.<event_type>` 로 제각각이다.)

무엇을 하는가
    발행 직후 같은 봉투를 07 의 수집 엔드포인트(POST /api/v1/audit/events)로 한 번 더
    보낸다. 이 엔드포인트는 Redis 구독과 **동일한 수집 함수**(`ingest_event`)를 타므로
    저장·프로젝션 경로가 달라지지 않는다.

안전장치
    - 데몬 스레드에서 비동기 전송 → API 응답 지연 없음. 07 이 죽어 있어도 무해.
    - 07 은 `event_id` 로 중복을 제거하므로, 실제 Redis 를 붙여 Pub/Sub 과 이 싱크가
      동시에 동작해도 이벤트가 두 번 저장되지 않는다.
    - `AUDIT_SINK_ENABLED=false` 로 끌 수 있고, `AUDIT_SINK_URL` 로 주소를 바꾼다.
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

log = logging.getLogger("audit-sink")

_DEFAULT_URL = "http://127.0.0.1:8007"
_PATH = "/api/v1/audit/events"
_TIMEOUT = float(os.getenv("AUDIT_SINK_TIMEOUT", "2.0"))

# 전송은 요청 스레드를 막지 않는다 (워커 2개면 PoC 트래픽에 충분).
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="audit-sink")


def enabled() -> bool:
    return os.getenv("AUDIT_SINK_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def endpoint() -> str:
    return os.getenv("AUDIT_SINK_URL", _DEFAULT_URL).rstrip("/") + _PATH


def to_dict(envelope: Any) -> dict | None:
    """봉투를 JSON 직렬화 가능한 dict 로 정규화한다 (pydantic 모델 / dict 모두 허용)."""
    if envelope is None:
        return None
    if isinstance(envelope, dict):
        return envelope
    dump = getattr(envelope, "model_dump", None)  # pydantic v2
    if callable(dump):
        return dump(mode="json")
    dump = getattr(envelope, "dict", None)  # pydantic v1 호환
    if callable(dump):
        return dump()
    return None


def _post(body: bytes) -> None:
    req = urllib_request.Request(
        endpoint(),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=_TIMEOUT) as response:
            response.read()
    except (urllib_error.URLError, OSError, TimeoutError) as exc:
        # 감사 서비스가 내려가 있어도 발행 서비스는 계속 동작해야 한다.
        log.debug("audit sink 전송 실패: %s", exc)


def forward(envelope: Any) -> None:
    """이벤트 봉투 1건을 감사 서비스로 비동기 전송한다 (실패해도 예외를 올리지 않음)."""
    if not enabled():
        return
    payload = to_dict(envelope)
    if not payload or not payload.get("event_type"):
        return
    try:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        _pool.submit(_post, body)
    except Exception as exc:  # pragma: no cover - 직렬화/스케줄 실패 방어
        log.debug("audit sink 스케줄 실패: %s", exc)
