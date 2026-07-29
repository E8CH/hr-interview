"""Alembic 실행 환경

접속 URL 은 alembic.ini 가 아니라 app.config(.env 의 DATABASE_URL)에서 가져온다.
PoC(SQLite)와 프로덕션(PostgreSQL) 모두 같은 명령으로 동작하게 하기 위함이다.
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from app.config import settings          # noqa: E402
from app.infrastructure.db import Base   # noqa: E402

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

#: SQLite 는 ALTER 제약이 많아 배치 모드가 필요하다
_render_as_batch = settings.database_url.startswith("sqlite")


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_render_as_batch,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_render_as_batch,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
