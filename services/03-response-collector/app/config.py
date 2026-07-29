"""환경 설정 — .env(python-dotenv) 로드"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

SERVICE_NAME = "response-collector"
SERVICE_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(SERVICE_ROOT / ".env")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


class Settings:
    """PoC 모드 기본값: SQLite 파일 + fakeredis + 로컬 스토리지."""

    def __init__(self) -> None:
        self.service_port: int = int(os.getenv("SERVICE_PORT", "8003"))
        self.database_url: str = os.getenv("DATABASE_URL", "sqlite:///./resp_db.sqlite")
        self.redis_url: str = os.getenv("REDIS_URL", "fakeredis://")
        self.use_mock: bool = _as_bool(os.getenv("USE_MOCK"), True)
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO")
        self.storage_dir: Path = Path(os.getenv("STORAGE_DIR", "./storage")).resolve()
        self.notification_url: str = os.getenv(
            "NOTIFICATION_SERVICE_URL", "http://localhost:8006"
        )
        # 리마인더 폴링 주기(분) — 명세: 30분 주기
        self.reminder_poll_minutes: int = int(os.getenv("REMINDER_POLL_MINUTES", "30"))
        # 데모/검증용 초 단위 오버라이드 (0 이면 분 단위 설정을 사용)
        self.reminder_poll_seconds: int = int(os.getenv("REMINDER_POLL_SECONDS", "0"))
        self.enable_scheduler: bool = _as_bool(os.getenv("ENABLE_SCHEDULER"), True)
        # 폼 링크 생성용 베이스 URL
        self.form_base_url: str = os.getenv("FORM_BASE_URL", "http://localhost:8003")

    @property
    def outbox_dir(self) -> Path:
        return self.storage_dir / "outbox"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
