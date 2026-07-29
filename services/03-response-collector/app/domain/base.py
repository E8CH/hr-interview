"""SQLAlchemy 선언적 베이스 · 공통 컬럼 헬퍼"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def new_uuid() -> str:
    """UUID 문자열 PK (SQLite 호환)."""
    return str(uuid4())
