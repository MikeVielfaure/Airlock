"""flow source dataset reference

A flow gains an optional fixed source: a table it reads fresh on every run,
resolved the same way tco/computed already are — never pinned data like an
uploaded file, because the table's *content* is expected to change between
runs even though the flow itself doesn't. NULL keeps today's behaviour:
running still requires an uploaded file.

Revision ID: ca086ab7126b
Revises: 1a246bcc4795
Create Date: 2026-08-03 07:25:11.980287
"""
from alembic import op
import sqlalchemy as sa


revision = 'ca086ab7126b'
down_revision = '1a246bcc4795'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("flows", schema=None) as b:
        b.add_column(sa.Column("source_dataset_id", sa.String(length=32), nullable=True))
        b.create_foreign_key("fk_flows_source_dataset_id", "datasets",
                             ["source_dataset_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("flows", schema=None) as b:
        b.drop_constraint("fk_flows_source_dataset_id", type_="foreignkey")
        b.drop_column("source_dataset_id")
