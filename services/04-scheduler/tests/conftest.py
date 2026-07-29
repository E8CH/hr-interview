"""공통 pytest fixture

주의: app.infrastructure.db가 import 시점에 엔진을 만들기 때문에
DATABASE_URL은 app을 import하기 전(= 이 모듈 최상단)에 설정해야 한다.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DIR = Path(__file__).resolve().parent
TEST_DB = TEST_DIR / "_test_sched_db.sqlite"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["REDIS_URL"] = "fakeredis://"
os.environ["USE_MOCK"] = "true"

if TEST_DB.exists():  # 이전 실행 잔여물 제거
    TEST_DB.unlink()

from app import events  # noqa: E402
from app.infrastructure.event_bus import get_event_bus  # noqa: E402
from app.main import app  # noqa: E402
from app.services import mock_data  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_bus():
    bus = get_event_bus()
    bus.reset()
    events.reset_readiness()
    yield
    bus.reset()
    events.reset_readiness()


@pytest.fixture
def sample_round_id():
    return "R2026-Q3-01"


@pytest.fixture
def applicants():
    return mock_data.build_applicants()


@pytest.fixture
def interviewers():
    return mock_data.build_interviewers()


@pytest.fixture
def generated(client, sample_round_id):
    """v5로 생성된 스케줄 하나"""
    resp = client.post(
        "/api/v1/schedules/generate",
        json={"round_id": sample_round_id, "plan_id": "plan-fixture", "algorithm": "v5"},
    )
    assert resp.status_code == 201
    return resp.json()["data"]
