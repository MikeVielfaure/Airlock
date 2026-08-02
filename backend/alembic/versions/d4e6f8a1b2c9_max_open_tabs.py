"""environment profiles: an admin-set cap on open tabs

Revision ID: d4e6f8a1b2c9
Revises: c3f7a0d5e8b1
"""
from alembic import op
import sqlalchemy as sa

revision = "d4e6f8a1b2c9"
down_revision = "c3f7a0d5e8b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "environment_profiles",
        sa.Column("max_open_tabs", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("environment_profiles", "max_open_tabs")
