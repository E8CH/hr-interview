"""공통 pytest fixture

테스트는 임시 SQLite 파일 + fakeredis + 로컬 outbox 로 완전히 격리된다.
app 모듈 import 전에 환경변수를 세팅해야 하므로 os.environ 을 모듈 최상단에서 건드린다.
"""
from __future__ import annotations

import os

os.environ["USE_MOCK"] = "true"
os.environ["REDIS_URL"] = "fakeredis://"
os.environ["RETRY_DELAYS"] = "0,0,0"      # 테스트에서는 백오프 없이 즉시 재시도
os.environ["MAX_ATTEMPTS"] = "3"
os.environ["WORKER_ENABLED"] = "false"     # 백그라운드 워커는 테스트에서 직접 구동
os.environ["EVENT_LISTENER_ENABLED"] = "false"
os.environ["DISPATCH_ON_REQUEST"] = "false"
os.environ["BASE_URL"] = "http://testserver"
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.infrastructure.db import get_session_factory, init_db  # noqa: E402
from app.infrastructure.event_bus import reset_event_bus  # noqa: E402
from app.services.seed import seed_all  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """테스트별 임시 DB · storage 디렉토리."""
    db_path = tmp_path / "notif_test.sqlite"
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("STORAGE_DIR", str(storage))
    return {"db_path": db_path, "storage": storage}


@pytest.fixture
def bus(env):
    """비어 있는 이벤트 버스."""
    return reset_event_bus()


@pytest.fixture
def db(env, bus):
    """초기화 + seed 완료된 DB."""
    init_db(os.environ["DATABASE_URL"])
    session = get_session_factory()()
    try:
        seed_all(session)
    finally:
        session.close()
    return env


@pytest.fixture
def session(db):
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    """lifespan 을 태운 TestClient (DB 는 이미 초기화되어 재사용된다)."""
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sample_round_id():
    return "R2026-Q3-01"


@pytest.fixture
def invite_context():
    return {
        "name": "이지훈",
        "deadline": "2026-07-31 18:00",
        "form_link": "https://hr.lge.com/form/abc123",
    }
