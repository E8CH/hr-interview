"""DB 세션 관리 (SQLAlchemy 2.x · PoC 는 SQLite 파일)"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.domain.base import Base

_connect_args = {}
if settings.database_url.startswith("sqlite"):
    # FastAPI + APScheduler 스레드에서 같은 연결을 쓸 수 있게
    _connect_args["check_same_thread"] = False

engine: Engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):  # pragma: no cover - 드라이버 훅
    """SQLite 에서도 FK 제약을 적용."""
    module = type(dbapi_connection).__module__
    if "sqlite" in module:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_db() -> None:
    """테이블 생성 (PoC — Alembic 대신 create_all)."""
    # 모델 등록을 위해 import 필요
    from app.domain import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI 의존성."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """스케줄러/이벤트 핸들러 등 요청 밖 컨텍스트용."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
