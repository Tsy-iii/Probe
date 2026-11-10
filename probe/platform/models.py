from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class PlatformBase(DeclarativeBase):
    pass


class TestRun(PlatformBase):
    __tablename__ = "test_runs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','cancelling','recovering','passed','failed','error','timed_out','cancelled')", name="run_status_valid"),
        CheckConstraint("finished_at IS NULL OR status IN ('passed','failed','error','timed_out','cancelled')", name="run_finish_is_terminal"),
        UniqueConstraint("idempotency_key", name="run_idempotency_key_unique"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    suite_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    total_count: Mapped[int | None] = mapped_column(Integer)
    passed_count: Mapped[int | None] = mapped_column(Integer)
    failed_count: Mapped[int | None] = mapped_column(Integer)
    error_count: Mapped[int | None] = mapped_column(Integer)
    skipped_count: Mapped[int | None] = mapped_column(Integer)
    report: Mapped[dict | None] = mapped_column(JSONB)
    case_selection: Mapped[dict | None] = mapped_column(JSONB)
    rerun_of_id: Mapped[UUID | None] = mapped_column(ForeignKey("test_runs.id", name="run_rerun_source_fkey"))
    error_message: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60, server_default="60")
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    worker_id: Mapped[UUID | None] = mapped_column()
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("run_attempts.id", use_alter=True, name="run_active_attempt_fkey"))
    report_attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("run_attempts.id", use_alter=True, name="run_report_attempt_fkey"))


class RunAttempt(PlatformBase):
    __tablename__ = "run_attempts"
    __table_args__ = (UniqueConstraint("run_id", "number", name="attempt_number_unique"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("test_runs.id"))
    number: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    process_id: Mapped[int | None] = mapped_column(Integer)
    isolation_schema: Mapped[str | None] = mapped_column(String(40))
    stop_reason: Mapped[str | None] = mapped_column(String(40))
    cleanup_done: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    exit_code: Mapped[int | None] = mapped_column(Integer)
    report: Mapped[dict | None] = mapped_column(JSONB)


class WorkerRecord(PlatformBase):
    __tablename__ = "workers"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    process_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="online")
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
