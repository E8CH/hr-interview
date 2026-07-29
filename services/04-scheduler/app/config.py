"""서비스 설정 — .env(python-dotenv) 로드"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# .env 파일이 BOM(utf-8-sig)으로 저장되어 있어도 첫 키가 깨지지 않도록 처리
load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


class Settings:
    """PoC 모드 기본값: SQLite + fakeredis + 로컬 스토리지"""

    service_name: str = "scheduler"
    producer: str = "scheduler"
    service_port: int = int(os.getenv("SERVICE_PORT", "8004") or 8004)
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./sched_db.sqlite")
    redis_url: str = os.getenv("REDIS_URL", "fakeredis://")
    use_mock: bool = _bool(os.getenv("USE_MOCK"), True)
    storage_dir: Path = BASE_DIR / (os.getenv("STORAGE_DIR", "./storage").lstrip("./") or "storage")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    event_channel: str = "hr.events"

    # 외부 서비스 (USE_MOCK=false 일 때만 사용)
    distributor_url: str = os.getenv("DISTRIBUTOR_URL", "http://localhost:8002")
    response_collector_url: str = os.getenv("RESPONSE_COLLECTOR_URL", "http://localhost:8003")

    def ensure_storage(self) -> Path:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        return self.storage_dir


settings = Settings()
