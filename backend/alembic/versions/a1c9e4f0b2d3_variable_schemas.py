"""variable schemas — known tables/endpoints on a connection point

Revision ID: a1c9e4f0b2d3
Revises: f4b1c7a92e6d
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a1c9e4f0b2d3"
down_revision = "f4b1c7a92e6d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "variable_schemas",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("variable_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("schema_json",
                  sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["variable_id"], ["variables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("variable_id", "name", name="uq_variable_schema"),
    )
    with op.batch_alter_table("variable_schemas") as b:
        b.create_index(b.f("ix_variable_schemas_variable_id"), ["variable_id"])


def downgrade() -> None:
    op.drop_table("variable_schemas")
