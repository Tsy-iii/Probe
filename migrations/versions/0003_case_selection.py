"""Preserve the submitted pytest case selection without inventing legacy history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_case_selection"
down_revision = "0002_reliable_runs"


def upgrade():
    op.add_column("test_runs", sa.Column("case_selection", JSONB(), nullable=True))


def downgrade():
    raise RuntimeError("Destructive downgrade is intentionally unsupported; restore a reviewed backup")
