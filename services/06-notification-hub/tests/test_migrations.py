"""Alembic 마이그레이션 ↔ 모델 정합성 (공통 계약 §10 migrations/)"""
from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.domain.models import Base

BASE_DIR = Path(__file__).resolve().parents[1]


def _alembic_config(url: str) -> Config:
    """alembic.ini 의 로깅 설정을 건드리지 않고 프로그램적으로 구성한다."""
    config = Config()
    config.set_main_option("script_location", str(BASE_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    db_path = tmp_path / "migrate.sqlite"
    url = f"sqlite:///{db_path.as_posix()}"
    # migrations/env.py 는 settings.database_url 을 읽는다
    monkeypatch.setenv("DATABASE_URL", url)
    return url, _alembic_config(url)


def test_upgrade_head_creates_all_tables(migrated):
    url, config = migrated
    command.upgrade(config, "head")

    inspector = inspect(create_engine(url))
    assert set(inspector.get_table_names()) >= set(Base.metadata.tables)


def test_migration_columns_match_models(migrated):
    """모델과 마이그레이션이 갈라지면 실패한다."""
    url, config = migrated
    command.upgrade(config, "head")
    inspector = inspect(create_engine(url))

    for name, table in Base.metadata.tables.items():
        migrated_columns = {c["name"] for c in inspector.get_columns(name)}
        model_columns = {c.name for c in table.columns}
        assert migrated_columns == model_columns, f"{name} 컬럼 불일치"

        pk = set(inspector.get_pk_constraint(name)["constrained_columns"])
        assert pk == {c.name for c in table.primary_key.columns}


def test_migration_creates_expected_indices(migrated):
    url, config = migrated
    command.upgrade(config, "head")
    inspector = inspect(create_engine(url))

    names = {ix["name"] for ix in inspector.get_indexes("notifications")}
    assert {"idx_notif_status", "idx_notif_correlation"} <= names


def test_downgrade_removes_schema(migrated):
    url, config = migrated
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    inspector = inspect(create_engine(url))
    remaining = set(inspector.get_table_names()) & set(Base.metadata.tables)
    assert remaining == set()
