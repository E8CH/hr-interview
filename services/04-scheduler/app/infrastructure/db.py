"""DB 엔진/세션 — PoC는 SQLite 파일, 프로덕션은 PostgreSQL"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.domain.base import Base

_connect_args = {}
if settings.database_url.startswith("sqlite"):
    # FastAPI TestClient / uvicorn 워커가 다른 스레드에서 세션을 쓰므로 해제
    _connect_args = {"check_same_thread": False}

engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def _add_missing_columns() -> None:
    """이미 만들어 둔 SQLite 파일에 모델에만 있는 컬럼을 덧붙인다.

    create_all 은 없는 테이블만 만들지 컬럼은 못 늘린다. 프로덕션은 Alembic 을
    쓰지만, PoC 는 DB 파일을 지우지 않고도 새 컬럼(예: interviewers.title)이
    붙도록 여기서 채워 넣는다. 기본값이 없는 필수 컬럼은 건드리지 않는다.
    """
    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    with engine.begin() as conn:
        for name, table in Base.metadata.tables.items():
            if name not in present:
                continue
            have = {c["name"] for c in inspector.get_columns(name)}
            for column in (c for c in table.columns if c.name not in have):
                default = getattr(column.server_default, "arg", None)
                if default is None and not column.nullable:
                    continue  # 값을 채울 수 없는 컬럼 — 사람이 손대야 한다
                ddl = f'ALTER TABLE "{name}" ADD COLUMN "{column.name}" ' \
                      f'{column.type.compile(engine.dialect)}'
                if default is not None:
                    quoted = str(default).replace("'", "''")
                    ddl += f" NOT NULL DEFAULT '{quoted}'"
                conn.execute(text(ddl))


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
    _add_missing_columns()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
