"""confidential columns: keys, holders, reveal trail

Revision ID: d5e91c3a7b28
Revises: c1f30ab7e592
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d5e91c3a7b28"
down_revision = "c1f30ab7e592"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "crypto_keys",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("wrapped_key", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "environment", name="uq_key_name_env"),
    )
    with op.batch_alter_table("crypto_keys") as b:
        b.create_index(b.f("ix_crypto_keys_name"), ["name"])
        b.create_index(b.f("ix_crypto_keys_environment"), ["environment"])
        b.create_index(b.f("ix_crypto_keys_active"), ["active"])

    op.create_table(
        "key_holders",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("key_id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["key_id"], ["crypto_keys.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_id", "user_id", name="uq_key_holder"),
    )
    with op.batch_alter_table("key_holders") as b:
        b.create_index(b.f("ix_key_holders_key_id"), ["key_id"])
        b.create_index(b.f("ix_key_holders_user_id"), ["user_id"])

    op.create_table(
        "reveal_events",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("user_email", sa.String(320), nullable=False),
        sa.Column("key_name", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("columns", JSONB, nullable=False),
        sa.Column("context", sa.String(200), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("reveal_events") as b:
        b.create_index(b.f("ix_reveal_events_user_id"), ["user_id"])
        b.create_index(b.f("ix_reveal_events_key_name"), ["key_name"])
        b.create_index(b.f("ix_reveal_events_created_at"), ["created_at"])


def downgrade() -> None:
    op.drop_table("reveal_events")
    op.drop_table("key_holders")
    op.drop_table("crypto_keys")
