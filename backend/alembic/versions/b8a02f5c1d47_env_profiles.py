"""environment profiles: what an environment exposes

Revision ID: b8a02f5c1d47
Revises: 9d4b71ec0a55
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b8a02f5c1d47"
down_revision = "9d4b71ec0a55"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "environment_profiles",
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("modules_json", JSONB, nullable=False),
        sa.Column("config_artefact_id", sa.String(32), nullable=False),
        sa.Column("config_version_no", sa.Integer(), nullable=True),
        sa.Column("config_locked", sa.Boolean(), nullable=False),
        sa.Column("tco_artefact_id", sa.String(32), nullable=False),
        sa.Column("tco_editable", sa.Boolean(), nullable=False),
        sa.Column("actions_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("environment_profiles")
