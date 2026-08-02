"""work sessions — the interactive workbench, persisted instead of in-memory

Revision ID: c3f7a0d5e8b1
Revises: a1c9e4f0b2d3
"""
from alembic import op
import sqlalchemy as sa

revision = "c3f7a0d5e8b1"
down_revision = "a1c9e4f0b2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_sessions",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("touched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("work_sessions") as b:
        b.create_index(b.f("ix_work_sessions_touched_at"), ["touched_at"])


def downgrade() -> None:
    op.drop_table("work_sessions")
