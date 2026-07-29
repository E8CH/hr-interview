"""notif_db 초기 스키마 (templates · notifications · channels · dead_letters)

Revision ID: 0001
Revises:
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "templates",
        sa.Column("template_id", sa.String(64), primary_key=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "notifications",
        sa.Column("notification_id", sa.String(36), primary_key=True),
        sa.Column("template_id", sa.String(64), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("recipient", sa.String(255), nullable=False),
        sa.Column("cc", sa.JSON(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("round_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
    )
    op.create_index("idx_notif_status", "notifications", ["status", "created_at"])
    op.create_index("idx_notif_correlation", "notifications", ["correlation_id"])

    op.create_table(
        "channels",
        sa.Column("channel_id", sa.String(64), primary_key=True),
        sa.Column("channel_type", sa.String(16), nullable=False),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
    )

    op.create_table(
        "dead_letters",
        sa.Column("dead_letter_id", sa.String(36), primary_key=True),
        sa.Column("notification_id", sa.String(36), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("recipient", sa.String(255), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_dead_letters_notification_id", "dead_letters", ["notification_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_dead_letters_notification_id", table_name="dead_letters")
    op.drop_table("dead_letters")
    op.drop_table("channels")
    op.drop_index("idx_notif_correlation", table_name="notifications")
    op.drop_index("idx_notif_status", table_name="notifications")
    op.drop_table("notifications")
    op.drop_table("templates")
