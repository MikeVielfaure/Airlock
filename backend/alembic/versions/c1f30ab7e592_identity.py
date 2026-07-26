"""identity: users, external identities, memberships, providers, sessions

Revision ID: c1f30ab7e592
Revises: b8a02f5c1d47
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c1f30ab7e592"
down_revision = "b8a02f5c1d47"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(200), nullable=True),
        sa.Column("is_superadmin", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users") as b:
        b.create_index(b.f("ix_users_email"), ["email"], unique=True)
        b.create_index(b.f("ix_users_active"), ["active"])

    op.create_table(
        "user_identities",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "subject", name="uq_identity"),
    )
    with op.batch_alter_table("user_identities") as b:
        b.create_index(b.f("ix_user_identities_user_id"), ["user_id"])
        b.create_index(b.f("ix_user_identities_provider"), ["provider"])
        b.create_index(b.f("ix_user_identities_subject"), ["subject"])

    op.create_table(
        "memberships",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("from_sso", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "environment", name="uq_membership"),
    )
    with op.batch_alter_table("memberships") as b:
        b.create_index(b.f("ix_memberships_user_id"), ["user_id"])
        b.create_index(b.f("ix_memberships_environment"), ["environment"])

    op.create_table(
        "auth_providers",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("client_id", sa.String(200), nullable=False),
        sa.Column("client_secret", sa.Text(), nullable=False),
        sa.Column("discovery_url", sa.Text(), nullable=False),
        sa.Column("authorize_url", sa.Text(), nullable=False),
        sa.Column("token_url", sa.Text(), nullable=False),
        sa.Column("jwks_url", sa.Text(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("scopes", sa.String(200), nullable=False),
        sa.Column("groups_claim", sa.String(64), nullable=False),
        sa.Column("claim_mappings", JSONB, nullable=False),
        sa.Column("auto_provision", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("auth_providers") as b:
        b.create_index(b.f("ix_auth_providers_name"), ["name"], unique=True)

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_agent", sa.String(300), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("auth_sessions") as b:
        b.create_index(b.f("ix_auth_sessions_user_id"), ["user_id"])
        b.create_index(b.f("ix_auth_sessions_expires_at"), ["expires_at"])


def downgrade() -> None:
    op.drop_table("auth_sessions")
    op.drop_table("auth_providers")
    op.drop_table("memberships")
    op.drop_table("user_identities")
    op.drop_table("users")
