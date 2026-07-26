"""impersonation: a superadmin may borrow an identity to see what it sees

Revision ID: f3b81e07c265
Revises: e7c25d9f1a44
"""
from alembic import op
import sqlalchemy as sa

revision = "f3b81e07c265"
down_revision = "e7c25d9f1a44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("auth_sessions") as b:
        b.add_column(sa.Column("impersonated_by", sa.String(32), nullable=False,
                               server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("auth_sessions") as b:
        b.drop_column("impersonated_by")
