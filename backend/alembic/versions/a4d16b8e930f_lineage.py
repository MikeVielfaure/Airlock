"""lineage: which artefact an artefact was derived from

Revision ID: a4d16b8e930f
Revises: f3b81e07c265
"""
from alembic import op
import sqlalchemy as sa

revision = "a4d16b8e930f"
down_revision = "f3b81e07c265"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("artefacts") as b:
        b.add_column(sa.Column("derived_from", sa.String(32), nullable=False,
                               server_default=""))
        b.add_column(sa.Column("derived_from_version", sa.Integer(), nullable=True))
        b.create_index(b.f("ix_artefacts_derived_from"), ["derived_from"])


def downgrade() -> None:
    with op.batch_alter_table("artefacts") as b:
        b.drop_index(b.f("ix_artefacts_derived_from"))
        b.drop_column("derived_from_version")
        b.drop_column("derived_from")
