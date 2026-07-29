"""서비스 설정 — .env 로드 (python-dotenv)

PoC 모드 기본값:
    DATABASE_URL=sqlite:///./audit_db.sqlite
    REDIS_URL=fakeredis://
    USE_MOCK=true
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# services/07-audit-analytics/
BASE_DIR = Path(__file__).resolve().parent.parent

# .env는 이미 생성되어 있음 (UTF-8 BOM 포함 가능 → encoding 지정)
load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """환경변수 기반 설정. 인스턴스화 시점에 os.environ을 읽는다."""

    def __init__(self) -> None:
        self.service_name: str = "07-audit-analytics"
        self.service_port: int = int(os.getenv("SERVICE_PORT", "8007"))
        self.database_url: str = os.getenv("DATABASE_URL", "sqlite:///./audit_db.sqlite")
        self.redis_url: str = os.getenv("REDIS_URL", "fakeredis://")
        self.use_mock: bool = _as_bool(os.getenv("USE_MOCK"), True)
        self.storage_dir: Path = Path(os.getenv("STORAGE_DIR", "./storage"))
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()

        # 이벤트 채널 프리픽스 — 07이 이벤트를 *발행할* 때와 테스트에서 쓴다.
        # 00_SHARED_CONTRACT.md에 채널명 규약이 없어 07 명세의 `hr.*`를 따른다.
        self.event_channel_prefix: str = os.getenv("EVENT_CHANNEL_PREFIX", "hr")

        # 구독 패턴 — 기본 `*` (모든 채널).
        # 공통 계약이 채널명을 규정하지 않은 탓에 발행 측 스킴이 3종으로 갈렸다
        # (01: `hr-events` 고정 / 02: event_type 그대로 / 04: 설정값).
        # 07은 채널명이 아니라 **봉투의 event_type**으로만 라우팅하므로,
        # 넓게 받아 봉투로 거르는 편이 스킴 통일을 기다리는 것보다 안전하다.
        self.event_channel_pattern_override: str | None = os.getenv("EVENT_CHANNEL_PATTERN")

        # 다른 서비스 base URL (USE_MOCK=false일 때만 사용)
        self.scheduler_url: str = os.getenv("SCHEDULER_URL", "http://127.0.0.1:8004")
        self.response_collector_url: str = os.getenv(
            "RESPONSE_COLLECTOR_URL", "http://127.0.0.1:8003"
        )
        self.http_timeout_s: float = float(os.getenv("HTTP_TIMEOUT_S", "3.0"))

        # 이벤트 수집기 자동 기동 여부 (테스트에서는 끌 수 있음)
        self.enable_collector: bool = _as_bool(os.getenv("ENABLE_COLLECTOR"), True)

    @property
    def event_channel_pattern(self) -> str:
        """psubscribe 패턴. 기본 `*` — 발행 측 채널 스킴에 관계없이 수신한다.

        `hr.*`처럼 좁히려면 EVENT_CHANNEL_PATTERN으로 지정한다.
        """
        return self.event_channel_pattern_override or "*"

    def channel_for(self, event_type: str) -> str:
        """이벤트 타입별 발행 채널명 — `hr.RESPONSE_RECEIVED`"""
        return f"{self.event_channel_prefix}.{event_type}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """테스트에서 환경변수 변경 후 호출"""
    get_settings.cache_clear()
