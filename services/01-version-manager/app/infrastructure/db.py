"""DB 세션/엔진 (SQLAlchemy 2.x, SQLite PoC)."""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    connect_args = {}
    if url.startswith("sqlite"):
        # FastAPI + TestClient 멀티스레드 대응
        connect_args = {"check_same_thread": False}
    return create_engine(url, connect_args=connect_args, future=True)


_settings = get_settings()
engine = _make_engine(_settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """테이블 생성 (Alembic 미사용 PoC)."""
    # 모델 등록을 위해 import
    from app.domain import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_session() -> Iterator[Session]:
    """FastAPI 의존성."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
