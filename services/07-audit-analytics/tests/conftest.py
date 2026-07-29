"""공통 pytest fixture

테스트마다 임시 SQLite 파일을 쓰고, 전역 싱글턴(설정·엔진·이벤트버스·수집기)을
초기화해 완전히 격리한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import reset_settings_cache
from app.infrastructure.db import init_db, new_session, reset_engine
from app.infrastructure.event_bus import reset_event_bus
from app.services import event_collector as collector_module


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    db_path = (tmp_path / "audit_db.sqlite").as_posix()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("REDIS_URL", "fakeredis://")
    monkeypatch.setenv("USE_MOCK", "true")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    # 기본은 수집기 비활성 — 구독 루프가 필요한 테스트만 명시적으로 켠다
    monkeypatch.setenv("ENABLE_COLLECTOR", "false")

    reset_settings_cache()
    reset_engine()
    reset_event_bus()
    collector_module.reset_collector()
    collector_module.STATS.__init__()  # 카운터 초기화

    init_db()
    yield
    reset_engine()
    reset_settings_cache()
    reset_event_bus()
    collector_module.reset_collector()


@pytest.fixture
def session():
    db = new_session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sample_round_id():
    return "R2026-Q3-01"


@pytest.fixture
def seeded(session):
    """데모 회차 + baseline이 주입된 세션"""
    from app.services.demo_data import seed_all

    seed_all(session)
    return session


@pytest.fixture
def seeded_client(client):
    """데모 데이터가 주입된 TestClient"""
    from app.services.demo_data import seed_all

    db = new_session()
    try:
        seed_all(db)
    finally:
        db.close()
    return client


def make_event(
    event_type: str,
    round_id: str = "R2026-Q3-01",
    payload: dict | None = None,
    minutes: float = 0.0,
    base: datetime | None = None,
    producer: str = "test",
    correlation_id: str | None = None,
    event_id: str | None = None,
) -> dict:
    """테스트용 이벤트 봉투 빌더"""
    base = base or datetime(2026, 7, 29, 9, 0, 0)
    return {
        "event_id": event_id or str(uuid4()),
        "event_type": event_type,
        "timestamp": (base + timedelta(minutes=minutes)).isoformat(),
        "round_id": round_id,
        "producer": producer,
        "correlation_id": correlation_id or "corr-test",
        "payload": payload or {},
    }


@pytest.fixture
def event_factory():
    return make_event
