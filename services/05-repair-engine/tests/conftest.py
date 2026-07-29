"""공통 pytest fixture

app 을 import 하기 **전에** 테스트용 환경변수를 세팅한다.
(app.config 는 import 시점에 .env 를 읽지만, 이미 설정된 os.environ 을 덮어쓰지 않는다)
"""
import os
import sys
import tempfile
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

_TMP_DB = Path(tempfile.mkdtemp(prefix="repair-test-")) / "repair_test.sqlite"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB.as_posix()}"
os.environ["USE_MOCK"] = "true"
os.environ["REDIS_URL"] = "fakeredis://"
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.events import register_subscribers  # noqa: E402
from app.infrastructure.db import Base, SessionLocal, engine, init_db  # noqa: E402
from app.infrastructure.event_bus import event_bus  # noqa: E402
from app.main import app  # noqa: E402
from app.services.scheduler_client import build_mock_schedule  # noqa: E402

SCHEDULE_ID = "SCH-TEST-0001"
ROUND_ID = "R2026-Q3-01"


@pytest.fixture(autouse=True)
def clean_db():
    """테스트마다 빈 DB · 빈 이벤트 이력에서 시작"""
    Base.metadata.drop_all(engine)
    init_db()
    event_bus.reset()
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client():
    register_subscribers()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sample_round_id():
    return ROUND_ID


@pytest.fixture
def schedule_id():
    return SCHEDULE_ID


@pytest.fixture
def snapshot():
    """65 배정 · 25 예비 슬롯의 결정적 mock 시간표"""
    return build_mock_schedule(SCHEDULE_ID, ROUND_ID)


@pytest.fixture
def noshow_13(snapshot):
    """5개 팀에 흩어진 노쇼 13명"""
    return [a.applicant_id for i, a in enumerate(snapshot.assignments) if i % 5 == 1][:13]
