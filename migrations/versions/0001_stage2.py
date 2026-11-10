"""Baseline the stage-2 task table."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_stage2"
down_revision = None


def upgrade():
    op.create_table("test_runs",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("suite_id", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("exit_code", sa.Integer()),
        *[sa.Column(name + "_count", sa.Integer()) for name in ["total", "passed", "failed", "error", "skipped"]],
        sa.Column("report", JSONB()), sa.Column("error_message", sa.Text()),
        sa.CheckConstraint("status IN ('queued','running','passed','failed','error')", name="run_status_valid"),
        sa.CheckConstraint("finished_at IS NULL OR started_at IS NOT NULL", name="run_finish_requires_start"),
    )
    op.create_index("ix_test_runs_status", "test_runs", ["status"])
    op.create_index("ix_test_runs_created_at", "test_runs", ["created_at"])


def downgrade():
    raise RuntimeError("Destructive downgrade is intentionally unsupported; restore a reviewed backup")
