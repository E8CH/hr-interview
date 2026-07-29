"""환경 설정 — .env 를 python-dotenv 로 로드"""
import os
from pathlib import Path

from dotenv import load_dotenv

SERVICE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(SERVICE_DIR / ".env")

SERVICE_NAME = "repair-engine"
PRODUCER = "repair-engine"


def _bool(key: str, default: str = "false") -> bool:
    return os.getenv(key, default).strip().lower() in ("1", "true", "yes")


class Settings:
    service_port: int = int(os.getenv("SERVICE_PORT", "8005"))
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./repair_db.sqlite")
    redis_url: str = os.getenv("REDIS_URL", "fakeredis://")
    use_mock: bool = _bool("USE_MOCK", "true")
    storage_dir: Path = SERVICE_DIR / os.getenv("STORAGE_DIR", "./storage").lstrip("./")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    scheduler_base_url: str = os.getenv("SCHEDULER_BASE_URL", "http://127.0.0.1:8004")

    def __init__(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
