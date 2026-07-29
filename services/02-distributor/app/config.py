"""환경설정 로더 + shared/contracts 경로 부트스트랩.

`shared/contracts`는 읽기 전용 공통 계약이므로 복사하지 않고 sys.path에 붙여 import한다.
설정값은 프로퍼티로 매번 os.environ을 읽는다 (테스트에서 monkeypatch 가능).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
SHARED_ROOT = REPO_ROOT / "shared"

if SHARED_ROOT.is_dir() and str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

load_dotenv(SERVICE_ROOT / ".env")

SERVICE_NAME = "distributor"


class Settings:
    """.env / 환경변수 기반 설정."""

    @property
    def service_port(self) -> int:
        return int(os.getenv("SERVICE_PORT", "8002"))

    @property
    def database_url(self) -> str:
        return os.getenv("DATABASE_URL", "sqlite:///./dist_db.sqlite")

    @property
    def redis_url(self) -> str:
        return os.getenv("REDIS_URL", "fakeredis://")

    @property
    def use_mock(self) -> bool:
        return os.getenv("USE_MOCK", "true").lower() in ("1", "true", "yes")

    @property
    def storage_dir(self) -> Path:
        raw = os.getenv("STORAGE_DIR", "./storage")
        path = Path(raw)
        if not path.is_absolute():
            path = SERVICE_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def log_level(self) -> str:
        return os.getenv("LOG_LEVEL", "INFO")

    @property
    def version_manager_url(self) -> str:
        return os.getenv("VERSION_MANAGER_URL", "http://localhost:8001")

    @property
    def mock_seed(self) -> int:
        return int(os.getenv("MOCK_SEED", "20260729"))

    @property
    def master_xlsx(self) -> Path | None:
        """USE_MOCK=true 일 때 사용할 실제 취합파일 경로 (없으면 합성 데이터)."""
        raw = os.getenv("MASTER_XLSX", "./data/취합파일.xlsx")
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = SERVICE_ROOT / path
        return path if path.exists() else None


settings = Settings()
