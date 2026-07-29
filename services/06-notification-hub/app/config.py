"""환경 설정 — .env 로드 (python-dotenv)

PoC 모드 기본값: SQLite 파일 · fakeredis · 로컬 storage 디렉토리.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw is not None else default
    except ValueError:
        return default


class Settings:
    """환경변수를 매 접근마다 읽는 얇은 설정 객체.

    속성이 아니라 property 로 노출하기 때문에 테스트에서 monkeypatch.setenv 로
    값을 바꾸면 즉시 반영된다.
    """

    SERVICE_NAME = "notification-hub"
    PRODUCER = "notification-hub"

    @property
    def service_port(self) -> int:
        return _int("SERVICE_PORT", 8006)

    @property
    def database_url(self) -> str:
        return os.getenv("DATABASE_URL", "sqlite:///./notif_db.sqlite")

    @property
    def redis_url(self) -> str:
        return os.getenv("REDIS_URL", "fakeredis://")

    @property
    def use_mock(self) -> bool:
        return _bool("USE_MOCK", True)

    @property
    def storage_dir(self) -> Path:
        raw = os.getenv("STORAGE_DIR", "./storage")
        path = Path(raw)
        if not path.is_absolute():
            path = BASE_DIR / path
        return path

    @property
    def outbox_dir(self) -> Path:
        return self.storage_dir / "outbox"

    @property
    def log_level(self) -> str:
        return os.getenv("LOG_LEVEL", "INFO")

    # --- 발송 파이프라인 ---
    @property
    def max_attempts(self) -> int:
        return _int("MAX_ATTEMPTS", 3)

    @property
    def retry_delays(self) -> list[float]:
        """재시도 백오프(초). 명세: 1회차 즉시 · 2회차 30초 · 3회차 5분."""
        raw = os.getenv("RETRY_DELAYS")
        if raw:
            return [float(x) for x in raw.split(",") if x.strip()]
        return [0.0, 30.0, 300.0]

    @property
    def worker_enabled(self) -> bool:
        """백그라운드 재시도 워커 기동 여부 (테스트에서는 off)."""
        return _bool("WORKER_ENABLED", True)

    @property
    def worker_interval(self) -> float:
        return _float("WORKER_INTERVAL", 1.0)

    @property
    def dispatch_on_request(self) -> bool:
        """요청 직후 1회차 발송을 즉시 시도할지 여부."""
        return _bool("DISPATCH_ON_REQUEST", True)

    @property
    def event_listener_enabled(self) -> bool:
        return _bool("EVENT_LISTENER_ENABLED", True)

    # --- 채널 ---
    @property
    def base_url(self) -> str:
        """트래킹 픽셀 URL 생성용 공개 주소."""
        return os.getenv("BASE_URL", f"http://localhost:{self.service_port}")

    @property
    def smtp_host(self) -> str:
        return os.getenv("SMTP_HOST", "localhost")

    @property
    def smtp_port(self) -> int:
        return _int("SMTP_PORT", 1025)

    @property
    def smtp_user(self) -> str | None:
        return os.getenv("SMTP_USER")

    @property
    def smtp_password(self) -> str | None:
        return os.getenv("SMTP_PASSWORD")

    @property
    def mail_from(self) -> str:
        return os.getenv("MAIL_FROM", "hr-noreply@lge.com")

    @property
    def hr_alert_email(self) -> str:
        return os.getenv("HR_ALERT_EMAIL", "hr-team@lge.com")


settings = Settings()
