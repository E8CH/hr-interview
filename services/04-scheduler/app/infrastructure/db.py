"""DB 엔진/세션 — PoC는 SQLite 파일, 프로덕션은 PostgreSQL"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.domain.base import Base

_connect_args = {}
if settings.database_url.startswith("sqlite"):
    # FastAPI TestClient / uvicorn 워커가 다른 스레드에서 세션을 쓰므로 해제
    _connect_args = {"check_same_thread": False}

engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """테이블 생성 (PoC — Alembic 대신 create_all)"""
    # 모든 모델을 임포트해야 metadata에 등록된다
    from app.domain import (  # noqa: F401
        assignment,
        interviewer,
        round_interviewer,
        schedule,
    )

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
