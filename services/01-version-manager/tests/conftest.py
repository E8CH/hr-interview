"""공통 pytest fixture — 격리된 임시 DB/스토리지 사용.

app 모듈 import 전에 환경변수를 설정해 테스트 전용 SQLite/스토리지를 사용한다.
"""
import os
import tempfile
from pathlib import Path

import pytest

# --- app import 전에 테스트 환경 구성 ---
_TMP = Path(tempfile.mkdtemp(prefix="vm_test_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.sqlite').as_posix()}"
os.environ["STORAGE_DIR"] = str(_TMP / "storage")
os.environ["REDIS_URL"] = "fakeredis://"
os.environ["USE_MOCK"] = "true"
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.infrastructure.db import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def fresh_db():
    """각 테스트마다 테이블을 새로 만든다."""
    init_db()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sample_round_id():
    return "R2026-Q3-01"


@pytest.fixture
def master_bytes():
    return (FIXTURES / "master.xlsx").read_bytes()


@pytest.fixture
def team_files():
    """{team_name: bytes} 5개 팀 배포본."""
    out = {}
    for p in sorted(FIXTURES.glob("team_*.xlsx")):
        team = p.stem.replace("team_", "")
        out[team] = p.read_bytes()
    return out
