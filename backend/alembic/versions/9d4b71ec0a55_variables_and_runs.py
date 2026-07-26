"""connection points (variables) and the operations journal

Revision ID: 9d4b71ec0a55
Revises: 7f2ce4a91b30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "9d4b71ec0a55"
down_revision = "7f2ce4a91b30"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "variables",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("graph_id", sa.String(32), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("secret", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "scope", "environment", "graph_id", "node_id",
                            name="uq_variable_scope"),
    )
    with op.batch_alter_table("variables") as b:
        b.create_index(b.f("ix_variables_name"), ["name"])
        b.create_index(b.f("ix_variables_scope"), ["scope"])
        b.create_index(b.f("ix_variables_environment"), ["environment"])
        b.create_index(b.f("ix_variables_graph_id"), ["graph_id"])

    op.create_table(
        "flow_runs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("graph_id", sa.String(32), nullable=False),
        sa.Column("graph_name", sa.String(200), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("params_json", JSONB, nullable=False),
        sa.Column("graph_json", JSONB, nullable=False),
        sa.Column("snapshot_json", JSONB, nullable=False),
        sa.Column("messages_json", JSONB, nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("error_node", sa.String(64), nullable=False),
        sa.Column("rows_out", sa.Integer(), nullable=False),
        sa.Column("ms", sa.Integer(), nullable=False),
        sa.Column("replay_of", sa.String(32), nullable=False),
        sa.Column("replay_mode", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("flow_runs") as b:
        b.create_index(b.f("ix_flow_runs_status"), ["status"])
        b.create_index(b.f("ix_flow_runs_started_at"), ["started_at"])
        b.create_index(b.f("ix_flow_runs_graph_id"), ["graph_id"])
        b.create_index(b.f("ix_flow_runs_environment"), ["environment"])
        b.create_index(b.f("ix_flow_runs_replay_of"), ["replay_of"])

    op.create_table(
        "flow_run_steps",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("node_type", sa.String(32), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("ms", sa.Integer(), nullable=False),
        sa.Column("records", sa.Integer(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("meta_json", JSONB, nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["flow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("flow_run_steps") as b:
        b.create_index(b.f("ix_flow_run_steps_run_id"), ["run_id"])


def downgrade() -> None:
    op.drop_table("flow_run_steps")
    op.drop_table("flow_runs")
    op.drop_table("variables")
