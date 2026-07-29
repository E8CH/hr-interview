"""환경설정 로드 (.env → python-dotenv)."""
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

_SERVICE_ROOT = Path(__file__).resolve().parents[1]

# .env 로드 (이미 존재). 테스트 시 monkeypatch가 우선하도록 override=False.
load_dotenv(_SERVICE_ROOT / ".env", override=False)


class Settings:
    def __init__(self) -> None:
        self.service_name = "version-manager"
        self.service_port = int(os.getenv("SERVICE_PORT", "8001"))
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./version_db.sqlite")
        self.redis_url = os.getenv("REDIS_URL", "fakeredis://")
        self.use_mock = os.getenv("USE_MOCK", "true").lower() == "true"
        self.storage_dir = os.getenv("STORAGE_DIR", "./storage")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir)
        if not p.is_absolute():
            p = _SERVICE_ROOT / p
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()
