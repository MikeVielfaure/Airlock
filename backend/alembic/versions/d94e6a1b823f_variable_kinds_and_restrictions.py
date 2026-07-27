"""variable kinds and restrictions — structured connection points

Revision ID: d94e6a1b823f
Revises: c8a4f21e0d6b
"""
from alembic import op
import sqlalchemy as sa

revision = "d94e6a1b823f"
down_revision = "c8a4f21e0d6b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("variables") as b:
        b.add_column(sa.Column("kind", sa.String(16), nullable=False, server_default="value"))
        b.create_index(b.f("ix_variables_kind"), ["kind"])

    op.create_table(
        "variable_restrictions",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("variable_id", sa.String(32), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("granted_by", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["variable_id"], ["variables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("variable_id", "environment", name="uq_variable_restriction"),
    )
    with op.batch_alter_table("variable_restrictions") as b:
        b.create_index(b.f("ix_variable_restrictions_variable_id"), ["variable_id"])
        b.create_index(b.f("ix_variable_restrictions_environment"), ["environment"])


def downgrade() -> None:
    op.drop_table("variable_restrictions")
    with op.batch_alter_table("variables") as b:
        b.drop_index(b.f("ix_variables_kind"))
        b.drop_column("kind")
