"""archived rows don't block name reuse

The old constraints (uq_artefact_kind_name_env, uq_dataset_name_env,
ix_flows_name) covered every row, archived or not — so archiving "tco" left
the name permanently taken and a fresh "tco" could never be created again.
Archiving is meant to hide a thing, not reserve its name forever. Replaced
with partial unique indexes that only look at non-archived rows: two
archived "tco"s (or an archived one and a live one) can now share a name,
which is exactly what "archived = out of the way" should mean.

Also drops `ix_datasets_name`: a leftover *global* unique index on
`datasets.name` from before environments existed (3c1abdc6cc60). The later
`uq_dataset_name_env` was meant to relax that to "unique per environment"
but never actually removed the older, stricter index — so two environments
have never actually been able to each have their own table of the same
name, silently contradicting the per-environment scoping everywhere else.

Revision ID: 1a246bcc4795
Revises: d4e6f8a1b2c9
Create Date: 2026-08-02 21:39:05.293756
"""
from alembic import op
import sqlalchemy as sa


revision = '1a246bcc4795'
down_revision = 'd4e6f8a1b2c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("artefacts", schema=None) as b:
        b.drop_constraint("uq_artefact_kind_name_env", type_="unique")
    op.create_index(
        "ix_artefact_active_kind_name_env", "artefacts",
        ["kind", "name", "environment"], unique=True,
        postgresql_where=sa.text("NOT archived"),
        sqlite_where=sa.text("NOT archived"),
    )

    with op.batch_alter_table("datasets", schema=None) as b:
        b.drop_constraint("uq_dataset_name_env", type_="unique")
    op.drop_index("ix_datasets_name", table_name="datasets")
    op.create_index("ix_datasets_name", "datasets", ["name"])  # plain lookup index, not unique
    op.create_index(
        "ix_dataset_active_name_env", "datasets",
        ["name", "environment"], unique=True,
        postgresql_where=sa.text("NOT archived"),
        sqlite_where=sa.text("NOT archived"),
    )

    op.drop_index("ix_flows_name", table_name="flows")
    op.create_index(
        "ix_flow_active_name", "flows", ["name"], unique=True,
        postgresql_where=sa.text("NOT archived"),
        sqlite_where=sa.text("NOT archived"),
    )


def downgrade() -> None:
    op.drop_index("ix_flow_active_name", table_name="flows")
    op.create_index("ix_flows_name", "flows", ["name"], unique=True)

    op.drop_index("ix_dataset_active_name_env", table_name="datasets")
    op.drop_index("ix_datasets_name", table_name="datasets")
    op.create_index("ix_datasets_name", "datasets", ["name"], unique=True)
    with op.batch_alter_table("datasets", schema=None) as b:
        b.create_unique_constraint("uq_dataset_name_env", ["name", "environment"])

    op.drop_index("ix_artefact_active_kind_name_env", table_name="artefacts")
    with op.batch_alter_table("artefacts", schema=None) as b:
        b.create_unique_constraint("uq_artefact_kind_name_env", ["kind", "name", "environment"])
