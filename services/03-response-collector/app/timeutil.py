"""시각 유틸

SQLite 는 타임존을 보존하지 않는다. 서비스 내부에서는 **naive UTC** 로 통일하고,
API 경계(입력/출력)에서만 tz-aware 로 변환한다.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """현재 시각 (naive UTC)"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt: datetime | None) -> datetime | None:
    """tz-aware/naive 혼재 입력을 naive UTC 로 정규화."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def to_aware_utc(dt: datetime | None) -> datetime | None:
    """응답 직렬화용 — naive UTC 를 tz-aware UTC 로."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hours_between(start: datetime, end: datetime) -> float:
    """start → end 경과 시간(시간 단위). 두 값 모두 naive UTC 로 정규화 후 계산."""
    s = to_naive_utc(start)
    e = to_naive_utc(end)
    return (e - s).total_seconds() / 3600.0
