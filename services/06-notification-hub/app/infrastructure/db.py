"""DB 연결 — SQLAlchemy 2.x · PoC 는 SQLite 파일 기반"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.domain.models import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _make_engine(url: str) -> Engine:
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        # SQLite + 백그라운드 워커 스레드 조합을 위해 스레드 체크 해제
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def init_db(url: str | None = None) -> Engine:
    """엔진·세션 팩토리를 (재)생성하고 테이블을 만든다."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = _make_engine(url or settings.database_url)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(_engine)
    return _engine


def ensure_db() -> Engine:
    """아직 초기화되지 않았을 때만 엔진을 만든다 (테스트가 먼저 잡은 DB 를 보존)."""
    if _engine is None:
        return init_db()
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_db()
    assert _engine is not None
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        init_db()
    assert _session_factory is not None
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """commit/rollback 을 감싸는 컨텍스트 매니저."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI 의존성."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
