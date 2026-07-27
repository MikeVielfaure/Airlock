"""widen grant subject_kind — "environment" does not fit in 8 chars

Revision ID: f4b1c7a92e6d
Revises: d94e6a1b823f
"""
from alembic import op
import sqlalchemy as sa

revision = "f4b1c7a92e6d"
down_revision = "d94e6a1b823f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("dataset_grants") as b:
        b.alter_column("subject_kind", type_=sa.String(16), existing_type=sa.String(8))
    with op.batch_alter_table("artefact_grants") as b:
        b.alter_column("subject_kind", type_=sa.String(16), existing_type=sa.String(8))


def downgrade() -> None:
    with op.batch_alter_table("artefact_grants") as b:
        b.alter_column("subject_kind", type_=sa.String(8), existing_type=sa.String(16))
    with op.batch_alter_table("dataset_grants") as b:
        b.alter_column("subject_kind", type_=sa.String(8), existing_type=sa.String(16))
