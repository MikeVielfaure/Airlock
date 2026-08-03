"""flow source artefact reference

A flow gains an optional reference to a "source" artefact — a saved recipe
(dataset / external_db / api) attached as an extra named frame at run time
for the flow's computed artefact to join against in a sql_computed block.
Distinct from source_dataset_id (the flow's fixed primary input): this is
never the input, just another frame available to `FROM self LEFT JOIN ...`.
NULL means no such attachment, exactly like tco/computed today.

Revision ID: b6a1f9d34c72
Revises: ca086ab7126b
Create Date: 2026-08-03 16:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b6a1f9d34c72'
down_revision = 'ca086ab7126b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("flows", schema=None) as b:
        b.add_column(sa.Column("source_artefact_id", sa.String(length=32), nullable=True))
        b.add_column(sa.Column("source_version_no", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_flows_source_artefact_id", "artefacts",
                             ["source_artefact_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("flows", schema=None) as b:
        b.drop_constraint("fk_flows_source_artefact_id", type_="foreignkey")
        b.drop_column("source_version_no")
        b.drop_column("source_artefact_id")
