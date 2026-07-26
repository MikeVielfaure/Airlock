"""environments: scope artefacts and datasets

Revision ID: 7f2ce4a91b30
Revises: 3c1abdc6cc60
Create Date: 2026-07-24

Everything already stored lands in 'default', so an existing install keeps
behaving exactly as before. Uniqueness moves from (kind, name) to
(kind, name, environment): two environments may each hold their own "clients"
config without colliding.
"""
from alembic import op
import sqlalchemy as sa

revision = "7f2ce4a91b30"
down_revision = "3c1abdc6cc60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("artefacts", schema=None) as b:
        b.add_column(sa.Column("environment", sa.String(length=64),
                               nullable=False, server_default="default"))
        b.create_index(b.f("ix_artefacts_environment"), ["environment"], unique=False)
        b.drop_constraint("uq_artefact_kind_name", type_="unique")
        b.create_unique_constraint("uq_artefact_kind_name_env", ["kind", "name", "environment"])

    with op.batch_alter_table("datasets", schema=None) as b:
        b.add_column(sa.Column("environment", sa.String(length=64),
                               nullable=False, server_default="default"))
        b.create_index(b.f("ix_datasets_environment"), ["environment"], unique=False)
        b.create_unique_constraint("uq_dataset_name_env", ["name", "environment"])


def downgrade() -> None:
    with op.batch_alter_table("datasets", schema=None) as b:
        b.drop_constraint("uq_dataset_name_env", type_="unique")
        b.drop_index(b.f("ix_datasets_environment"))
        b.drop_column("environment")
    with op.batch_alter_table("artefacts", schema=None) as b:
        b.drop_constraint("uq_artefact_kind_name_env", type_="unique")
        b.create_unique_constraint("uq_artefact_kind_name", ["kind", "name"])
        b.drop_index(b.f("ix_artefacts_environment"))
        b.drop_column("environment")
