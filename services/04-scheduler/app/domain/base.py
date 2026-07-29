"""SQLAlchemy 2.x 선언적 베이스"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    """PostgreSQL gen_random_uuid() 대응 (SQLite 호환 String(36))"""
    return str(uuid4())
