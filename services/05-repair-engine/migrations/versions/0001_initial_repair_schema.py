"""initial repair schema

명세(bmad/05_repair_engine.md)의 4테이블 + schedule_snapshots.
PostgreSQL DDL 의 UUID -> String(36), JSONB -> JSON 으로 두 DB 모두 지원한다.

Revision ID: 0001
Revises:
Create Date: 2026-07-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "repair_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("round_id", sa.String(length=32), nullable=False),
        sa.Column("schedule_id", sa.String(length=36), nullable=False),
        sa.Column("trigger_type", sa.String(length=32), nullable=False),
        sa.Column("trigger_target", sa.String(length=64), nullable=False),
        sa.Column("reported_at", sa.DateTime(), nullable=False),
        sa.Column("reported_by", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("affected", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(op.f("ix_repair_events_round_id"), "repair_events",
                    ["round_id"], unique=False)

    op.create_table(
        "repair_plans",
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("plan_type", sa.String(length=16), nullable=False),
        sa.Column("rebooked_count", sa.Integer(), nullable=False),
        sa.Column("deferred_count", sa.Integer(), nullable=False),
        sa.Column("hard_violations", sa.Integer(), nullable=False),
        sa.Column("soft_penalty", sa.Integer(), nullable=False),
        sa.Column("plan_detail", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["repair_events.event_id"]),
        sa.PrimaryKeyConstraint("plan_id"),
    )
    op.create_index(op.f("ix_repair_plans_event_id"), "repair_plans",
                    ["event_id"], unique=False)

    op.create_table(
        "selected_plans",
        sa.Column("selection_id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("selected_by", sa.String(length=64), nullable=False),
        sa.Column("selected_at", sa.DateTime(), nullable=False),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("affected_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["repair_events.event_id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["repair_plans.plan_id"]),
        sa.PrimaryKeyConstraint("selection_id"),
    )
    op.create_index(op.f("ix_selected_plans_event_id"), "selected_plans",
                    ["event_id"], unique=False)

    op.create_table(
        "lock_map",
        sa.Column("schedule_id", sa.String(length=36), nullable=False),
        sa.Column("applicant_id", sa.String(length=32), nullable=False),
        sa.Column("lock_level", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("schedule_id", "applicant_id"),
    )

    # Service 04 시간표의 로컬 사본 (다른 서비스 DB 직접 접근 금지 규약 준수)
    op.create_table(
        "schedule_snapshots",
        sa.Column("schedule_id", sa.String(length=36), nullable=False),
        sa.Column("round_id", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("schedule_id"),
    )


def downgrade() -> None:
    op.drop_table("schedule_snapshots")
    op.drop_table("lock_map")
    op.drop_index(op.f("ix_selected_plans_event_id"), table_name="selected_plans")
    op.drop_table("selected_plans")
    op.drop_index(op.f("ix_repair_plans_event_id"), table_name="repair_plans")
    op.drop_table("repair_plans")
    op.drop_index(op.f("ix_repair_events_round_id"), table_name="repair_events")
    op.drop_table("repair_events")
