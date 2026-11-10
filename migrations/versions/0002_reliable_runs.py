"""Persist idempotency, leases, attempts and worker heartbeats."""
import hashlib
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_reliable_runs"
down_revision = "0001_stage2"


def upgrade():
    for name, column_type in [("idempotency_key", sa.String(100)), ("request_hash", sa.String(64)),
                              ("worker_id", sa.Uuid()), ("active_attempt_id", sa.Uuid()), ("report_attempt_id", sa.Uuid()),
                              ("lease_until", sa.DateTime(timezone=True)), ("cancel_requested_at", sa.DateTime(timezone=True))]:
        op.add_column("test_runs", sa.Column(name, column_type))
    for name, default in [("timeout_seconds", "60"), ("max_attempts", "2"), ("attempt_count", "0")]:
        op.add_column("test_runs", sa.Column(name, sa.Integer(), nullable=False, server_default=default))
    connection = op.get_bind()
    for row in connection.execute(sa.text("SELECT id,suite_id FROM test_runs")).mappings():
        fingerprint = hashlib.sha256(json.dumps({"suite_id": row["suite_id"], "timeout_seconds": 60}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        connection.execute(sa.text("UPDATE test_runs SET idempotency_key=:key,request_hash=:hash WHERE id=:id"),
                           {"key": "legacy-" + str(row["id"]), "hash": fingerprint, "id": row["id"]})
    op.alter_column("test_runs", "idempotency_key", nullable=False)
    op.alter_column("test_runs", "request_hash", nullable=False)
    op.create_unique_constraint("run_idempotency_key_unique", "test_runs", ["idempotency_key"])
    op.drop_constraint("run_status_valid", "test_runs", type_="check")
    op.drop_constraint("run_finish_requires_start", "test_runs", type_="check")
    op.create_check_constraint("run_status_valid", "test_runs", "status IN ('queued','running','cancelling','recovering','passed','failed','error','timed_out','cancelled')")
    op.create_check_constraint("run_finish_is_terminal", "test_runs", "finished_at IS NULL OR status IN ('passed','failed','error','timed_out','cancelled')")
    connection.execute(sa.text("UPDATE test_runs SET status='queued',error_message='Migrated unowned stage-2 run; queued for execution' WHERE status='running'"))
    op.create_table("run_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("run_id", sa.Uuid(), sa.ForeignKey("test_runs.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False), sa.Column("worker_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)), sa.Column("process_id", sa.Integer()),
        sa.Column("isolation_schema", sa.String(40)), sa.Column("stop_reason", sa.String(40)),
        sa.Column("cleanup_done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exit_code", sa.Integer()), sa.Column("report", JSONB()),
        sa.UniqueConstraint("run_id", "number", name="attempt_number_unique"),
    )
    op.create_foreign_key("run_active_attempt_fkey", "test_runs", "run_attempts", ["active_attempt_id"], ["id"])
    op.create_foreign_key("run_report_attempt_fkey", "test_runs", "run_attempts", ["report_attempt_id"], ["id"])
    op.create_table("workers",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("process_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    raise RuntimeError("Destructive downgrade is intentionally unsupported; restore a reviewed backup")
