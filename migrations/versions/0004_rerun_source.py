"""Link a new execution to its source without modifying historical results."""
from alembic import op
import sqlalchemy as sa

revision = "0004_rerun_source"
down_revision = "0003_case_selection"


def upgrade():
    op.add_column("test_runs", sa.Column("rerun_of_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("run_rerun_source_fkey", "test_runs", "test_runs", ["rerun_of_id"], ["id"])


def downgrade():
    raise RuntimeError("Destructive downgrade is intentionally unsupported; restore a reviewed backup")
