"""공통 pytest fixture

주의: `app.config` 가 import 되기 전에 환경변수를 세팅해야 한다
(load_dotenv 는 기존 os.environ 을 덮어쓰지 않으므로 여기서 지정한 값이 우선).
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="resp-collector-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test_resp_db.sqlite').as_posix()}"
os.environ["REDIS_URL"] = "fakeredis://"
os.environ["USE_MOCK"] = "true"
os.environ["STORAGE_DIR"] = str(_TMP / "storage")
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["FORM_BASE_URL"] = "http://testserver"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.domain.base import Base  # noqa: E402
from app.domain.models import Invitee, Request  # noqa: E402
from app.infrastructure.db import SessionLocal, engine  # noqa: E402
from app.infrastructure.event_bus import get_event_bus  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.schemas import InviteeIn  # noqa: E402
from app.services import request_service  # noqa: E402
from app.services.notification_client import (  # noqa: E402
    get_notification_client,
    reset_notification_client,
)
from app.timeutil import utcnow  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    """테스트마다 DB · 이벤트 이력 · outbox 초기화."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    get_event_bus().reset()
    reset_notification_client()
    outbox = get_notification_client().outbox_path
    if outbox.exists():
        outbox.unlink()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    with TestClient(fastapi_app) as c:
        yield c


@pytest.fixture
def bus():
    return get_event_bus()


@pytest.fixture
def notifier():
    return get_notification_client()


@pytest.fixture
def sample_round_id():
    return "R2026-Q3-01"


@pytest.fixture
def deadline():
    return datetime.now(timezone.utc) + timedelta(hours=72)


@pytest.fixture
def invitee_payloads():
    return [
        InviteeIn(
            name="이지훈",
            email="iv1@lge.com",
            team="AI솔루션팀",
            org="제1기술원",
            dept_leader_email="lead1@lge.com",
        ),
        InviteeIn(
            name="박서연",
            email="iv2@lge.com",
            team="AI솔루션팀",
            org="제3기술원",
            dept_leader_email="lead3@lge.com",
        ),
        InviteeIn(
            name="김도현",
            email="iv3@lge.com",
            team="배터리기술팀",
            org="제1기술원",
            dept_leader_email="lead1@lge.com",
        ),
    ]


@pytest.fixture
def created_request(db, sample_round_id, deadline, invitee_payloads) -> Request:
    """발송 완료된 요청 (초대자 3명)."""
    request, _ = request_service.create_request(
        db,
        round_id=sample_round_id,
        plan_id="plan-0001",
        deadline=deadline,
        invitees=invitee_payloads,
    )
    return request


@pytest.fixture
def first_invitee(db, created_request) -> Invitee:
    return created_request.invitees[0]


@pytest.fixture
def valid_payload():
    return {
        "job_role": "배터리 소재 연구",
        "available_slots": [
            {"day": "화", "hour": "10시"},
            {"day": "화", "hour": "11시"},
            {"day": "수", "hour": "14시"},
        ],
        "max_daily": 6,
        "backup_contact": "backup@lge.com",
        "notes": "8/5 오전 학회 발표",
    }


def shift_sent_at(db, request: Request, hours: float) -> Request:
    """요청 발송 시각을 과거로 이동 (리마인더 시나리오 구성용)."""
    request.sent_at = utcnow() - timedelta(hours=hours)
    db.commit()
    return request
