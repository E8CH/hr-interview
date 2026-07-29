"""이벤트 도메인 — API 입출력 스키마 · payload 필드 추출 헬퍼

payload는 서비스마다 자유 dict라서, 07은 "있으면 쓰고 없으면 폴백"
전략으로 방어적으로 읽는다. 봉투(envelope)만 계약으로 신뢰한다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

UNKNOWN_ORG = "미지정"


class IngestEvent(BaseModel):
    """POST /api/v1/audit/events — mock 이벤트 주입용 봉투.

    07은 이벤트를 발행하지 않는다. 이 엔드포인트는 PoC에서 다른 서비스의
    이벤트를 모사 주입해 수집 경로를 그대로 태우기 위한 것이다.
    """

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    round_id: str | None = None
    producer: str | None = None
    correlation_id: str | None = None
    payload: dict = Field(default_factory=dict)


class AuditQueryRequest(BaseModel):
    """POST /api/v1/audit/query"""

    round_id: str | None = None
    actor: str | None = None
    event_types: list[str] | None = None
    producer: str | None = None
    correlation_id: str | None = None
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    limit: int = 500
    offset: int = 0

    model_config = {"populate_by_name": True}


# --- payload 추출 헬퍼 -------------------------------------------------------


def pick(payload: dict | None, *keys: str, default: Any = None) -> Any:
    """payload에서 첫 번째로 존재하는 키의 값을 반환"""
    if not payload:
        return default
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return default


def pick_org(payload: dict | None) -> str:
    """조직명 추출 — 서비스별 키 명명 편차 흡수"""
    value = pick(payload, "org", "organization", "team", "team_name", "org_name")
    return str(value) if value else UNKNOWN_ORG


def pick_number(payload: dict | None, *keys: str, default: float = 0.0) -> float:
    value = pick(payload, *keys, default=None)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pick_opt_number(payload: dict | None, *keys: str) -> float | None:
    """숫자로 읽되, 키가 없거나 변환 불가면 None (0.0과 구분해야 할 때 사용)"""
    value = pick(payload, *keys, default=None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pick_int(payload: dict | None, *keys: str, default: int = 0) -> int:
    return int(pick_number(payload, *keys, default=default))


def parse_dt(value: Any) -> datetime | None:
    """ISO 문자열 / datetime → naive UTC datetime"""
    from app.infrastructure.db import to_naive_utc

    if value is None:
        return None
    if isinstance(value, datetime):
        return to_naive_utc(value)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return to_naive_utc(datetime.fromisoformat(text))
        except ValueError:
            return None
    return None
