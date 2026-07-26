"""artefact grants — an environment reading another's artefact

Revision ID: c8a4f21e0d6b
Revises: a4d16b8e930f
"""
from alembic import op
import sqlalchemy as sa

revision = "c8a4f21e0d6b"
down_revision = "a4d16b8e930f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artefact_grants",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("artefact_id", sa.String(32), nullable=False),
        sa.Column("subject_kind", sa.String(8), nullable=False, server_default="environment"),
        sa.Column("subject", sa.String(64), nullable=False),
        sa.Column("permission", sa.String(8), nullable=False, server_default="read"),
        sa.Column("granted_by", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artefact_id"], ["artefacts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artefact_id", "subject_kind", "subject",
                            name="uq_artefact_grant"),
    )
    with op.batch_alter_table("artefact_grants") as b:
        b.create_index(b.f("ix_artefact_grants_artefact_id"), ["artefact_id"])
        b.create_index(b.f("ix_artefact_grants_subject"), ["subject"])


def downgrade() -> None:
    op.drop_table("artefact_grants")
