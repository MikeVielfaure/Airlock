"""per-table permissions and ownership

Revision ID: e7c25d9f1a44
Revises: d5e91c3a7b28
"""
from alembic import op
import sqlalchemy as sa

revision = "e7c25d9f1a44"
down_revision = "d5e91c3a7b28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("datasets") as b:
        b.add_column(sa.Column("owner_id", sa.String(32), nullable=False,
                               server_default=""))
        b.add_column(sa.Column("is_managed", sa.Boolean(), nullable=False,
                               server_default=sa.false()))
        b.create_index(b.f("ix_datasets_owner_id"), ["owner_id"])

    op.create_table(
        "dataset_grants",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("dataset_id", sa.String(32), nullable=False),
        sa.Column("subject_kind", sa.String(8), nullable=False),
        sa.Column("subject", sa.String(64), nullable=False),
        sa.Column("permission", sa.String(8), nullable=False),
        sa.Column("granted_by", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "subject_kind", "subject",
                            name="uq_dataset_grant"),
    )
    with op.batch_alter_table("dataset_grants") as b:
        b.create_index(b.f("ix_dataset_grants_dataset_id"), ["dataset_id"])
        b.create_index(b.f("ix_dataset_grants_subject"), ["subject"])


def downgrade() -> None:
    op.drop_table("dataset_grants")
    with op.batch_alter_table("datasets") as b:
        b.drop_index(b.f("ix_datasets_owner_id"))
        b.drop_column("is_managed")
        b.drop_column("owner_id")
