import hashlib
import json
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import String, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import load_only

from probe.db import Database
from probe.platform.models import RunAttempt, TestRun, WorkerRecord
from probe.platform.schemas import AttemptOut, RunDetail, RunOut, SubmitResult
from probe.platform.data_views import view_session
from probe.platform.suites import SUITES


TERMINAL = {"passed", "failed", "error", "timed_out", "cancelled"}
STATUSES = ["queued", "running", "cancelling", "recovering", *sorted(TERMINAL)]


class SubmissionConflict(Exception):
    pass


def submission_hash(suite_id: str, timeout_seconds: int, case_ids: list[str] | None = None, rerun_of_id: UUID | None = None) -> str:
    content = {"suite_id": suite_id, "timeout_seconds": timeout_seconds}
    # 未指定用例时保留旧指纹格式；选择顺序不改变同一逻辑请求的含义。
    if case_ids is not None:
        content["case_ids"] = sorted(case_ids)
    if rerun_of_id is not None:
        content["rerun_of_id"] = str(rerun_of_id)
    payload = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class RunRepository:
    def __init__(self, database: Database):
        self.database = database

    def replay(self, suite_id, timeout_seconds, key, case_ids=None, rerun_of_id=None):
        if key is None:
            return None
        with self.database.sessions() as session:
            run = session.scalar(select(TestRun).where(TestRun.idempotency_key == key))
            if run is None:
                return None
            if run.request_hash != submission_hash(suite_id, timeout_seconds, case_ids, rerun_of_id):
                raise SubmissionConflict("Idempotency key was used with different execution parameters or source run")
            return SubmitResult(**RunOut.model_validate(run).model_dump(), replayed=True)

    def create(self, suite_id: str, timeout_seconds: int = 60, key: str | None = None, max_attempts: int = 2, *, case_ids=None, case_selection=None, rerun_of_id=None) -> SubmitResult:
        key = key or str(uuid4())
        fingerprint = submission_hash(suite_id, timeout_seconds, case_ids, rerun_of_id)
        with self.database.sessions.begin() as session:
            run_id = session.scalar(insert(TestRun).values(
                id=uuid4(), suite_id=suite_id, status="queued", idempotency_key=key,
                request_hash=fingerprint, timeout_seconds=timeout_seconds,
                max_attempts=max_attempts, attempt_count=0,
                case_selection=case_selection,
                rerun_of_id=rerun_of_id,
            ).on_conflict_do_nothing(index_elements=[TestRun.idempotency_key]).returning(TestRun.id))
            replayed = run_id is None
            run = session.scalar(select(TestRun).where(TestRun.idempotency_key == key))
            if run.request_hash != fingerprint:
                raise SubmissionConflict("Idempotency key was used with different execution parameters or source run")
            result = SubmitResult(**RunOut.model_validate(run).model_dump(), replayed=replayed)
        return result

    def get(self, run_id: UUID) -> RunDetail | None:
        with self.database.sessions() as session:
            run = session.get(TestRun, run_id)
            if run is None:
                return None
            attempts = session.scalars(select(RunAttempt).where(RunAttempt.run_id == run_id).order_by(RunAttempt.number)).all()
            return RunDetail(**RunOut.model_validate(run).model_dump(), report=run.report, attempts=[AttemptOut.model_validate(a) for a in attempts])

    def list(self, limit: int = 50, offset: int = 0, suite_id=None, status=None, q=None) -> dict:
        # 列表、匹配数量和全局统计来自同一只读快照，不读取大报告 JSONB。
        with view_session(self.database) as session:
            conditions = []
            if suite_id:
                conditions.append(TestRun.suite_id == suite_id)
            if status:
                conditions.append(TestRun.status == status)
            term = (q or "").strip()
            if term:
                matched_suites = [key for key, suite in SUITES.items() if term.lower() in key.lower() or term.lower() in suite.name.lower()]
                conditions.append(or_(cast(TestRun.id, String).icontains(term, autoescape=True), TestRun.suite_id.in_(matched_suites)))
            columns = [getattr(TestRun, name) for name in RunOut.model_fields]
            query = select(TestRun).options(load_only(*columns)).where(*conditions).order_by(TestRun.created_at.desc(), TestRun.id.desc()).offset(offset).limit(limit)
            runs = session.scalars(query).all()
            summary = {status: 0 for status in STATUSES}
            for status, count in session.execute(select(TestRun.status, func.count()).group_by(TestRun.status)):
                summary[status] = count
            matched = session.scalar(select(func.count()).select_from(TestRun).where(*conditions))
            return {"items": [RunOut.model_validate(run) for run in runs], "total": sum(summary.values()), "summary": summary, "matched_total": matched, "limit": limit, "offset": offset}

    def cancel(self, run_id: UUID) -> RunDetail | None:
        with self.database.sessions.begin() as session:
            run = session.scalar(select(TestRun).where(TestRun.id == run_id).with_for_update())
            if run is None:
                return None
            if run.status == "cancelled":
                pass
            elif run.status in TERMINAL:
                raise SubmissionConflict("A finished run cannot be cancelled")
            else:
                run.cancel_requested_at = run.cancel_requested_at or func.now()
                if run.status == "queued":
                    run.status, run.finished_at = "cancelled", func.now()
                elif run.status != "recovering":
                    run.status = "cancelling"
        return self.get(run_id)

    def register_worker(self, worker_id: UUID, process_id: int):
        with self.database.sessions.begin() as session:
            session.add(WorkerRecord(id=worker_id, process_id=process_id, status="online"))

    def heartbeat(self, worker_id: UUID, lease_seconds: float, active: tuple[UUID, UUID] | None) -> bool:
        with self.database.sessions.begin() as session:
            session.execute(update(WorkerRecord).where(WorkerRecord.id == worker_id).values(heartbeat_at=func.now()))
            if active is None:
                return True
            run_id, attempt_id = active
            result = session.execute(update(TestRun).where(
                TestRun.id == run_id, TestRun.worker_id == worker_id,
                TestRun.active_attempt_id == attempt_id,
                TestRun.status.in_(["running", "cancelling", "recovering"]),
                TestRun.lease_until > func.now(),
            ).values(lease_until=func.now() + timedelta(seconds=lease_seconds)))
            return result.rowcount == 1

    def offline(self, worker_id: UUID):
        with self.database.sessions.begin() as session:
            session.execute(update(WorkerRecord).where(WorkerRecord.id == worker_id).values(status="offline", heartbeat_at=func.now()))

    def system(self, lease_seconds: float) -> dict:
        with self.database.sessions() as session:
            count = session.scalar(select(func.count()).select_from(WorkerRecord).where(
                WorkerRecord.status == "online", WorkerRecord.heartbeat_at > func.now() - timedelta(seconds=lease_seconds),
            ))
            return {"online_workers": count, "queue": "postgresql", "lease_seconds": lease_seconds}

    def claim(self, worker_id: UUID, lease_seconds: float, requires_schema: set[str]) -> RunDetail | None:
        with self.database.sessions.begin() as session:
            run = session.scalar(select(TestRun).where(TestRun.status == "queued").order_by(TestRun.created_at, TestRun.id).with_for_update(skip_locked=True).limit(1))
            if run is None:
                return None
            attempt_id = uuid4()
            run.attempt_count += 1
            attempt = RunAttempt(id=attempt_id, run_id=run.id, number=run.attempt_count, worker_id=worker_id,
                                 isolation_schema="run_" + attempt_id.hex if run.suite_id in requires_schema else None)
            session.add(attempt)
            session.flush()  # Insert the attempt before the FK points at it.
            run.active_attempt_id, run.worker_id, run.status = attempt_id, worker_id, "running"
            run.started_at = run.started_at or func.now()
            run.lease_until = func.now() + timedelta(seconds=lease_seconds)
            run_id = run.id
        return self.get(run_id)

    def claim_recovery(self, worker_id: UUID, lease_seconds: float) -> RunDetail | None:
        with self.database.sessions.begin() as session:
            run = session.scalar(select(TestRun).where(
                TestRun.status.in_(["running", "cancelling", "recovering"]), TestRun.lease_until < func.now(),
            ).order_by(TestRun.lease_until).with_for_update(skip_locked=True).limit(1))
            if run is None:
                return None
            run.status, run.worker_id = "recovering", worker_id
            run.lease_until = func.now() + timedelta(seconds=lease_seconds)
            run_id = run.id
        return self.get(run_id)

    def control(self, run_id: UUID, attempt_id: UUID, worker_id: UUID) -> dict:
        with self.database.sessions() as session:
            row = session.execute(select(
                TestRun.active_attempt_id, TestRun.worker_id, TestRun.status,
                TestRun.cancel_requested_at, (TestRun.lease_until > func.now()).label("lease_valid"),
            ).where(TestRun.id == run_id)).mappings().first()
            if row is None:
                return {"owned": False, "cancelled": False}
            return {
                "owned": row["active_attempt_id"] == attempt_id and row["worker_id"] == worker_id and row["lease_valid"] and row["status"] in {"running", "cancelling"},
                "cancelled": row["cancel_requested_at"] is not None,
            }

    def set_process(self, run_id: UUID, attempt_id: UUID, worker_id: UUID, process_id: int) -> bool:
        with self.database.sessions.begin() as session:
            run = session.scalar(select(TestRun).where(TestRun.id == run_id).with_for_update())
            if run.active_attempt_id != attempt_id or run.worker_id != worker_id or run.status not in {"running", "cancelling"}:
                return False
            session.get(RunAttempt, attempt_id).process_id = process_id
            return True

    def finish(self, run_id: UUID, attempt_id: UUID, worker_id: UUID, *, status: str, exit_code: int | None,
               report: dict | None, reason: str | None = None, error_message: str | None = None, recovery: bool = False) -> bool:
        """Fence old attempts; commit the outcome only after process/schema cleanup."""
        with self.database.sessions.begin() as session:
            run = session.scalar(select(TestRun).where(TestRun.id == run_id).with_for_update())
            if run is None or run.active_attempt_id != attempt_id or run.worker_id != worker_id or run.status not in ({"recovering"} if recovery else {"running", "cancelling"}):
                return False
            lease_valid = session.scalar(select(TestRun.lease_until > func.now()).where(TestRun.id == run_id))
            if not lease_valid:
                return False
            attempt = session.get(RunAttempt, attempt_id)
            if run.cancel_requested_at is not None:
                status, reason = "cancelled", "cancelled"
            retry = reason == "worker_lost" and status != "cancelled" and run.attempt_count < run.max_attempts
            attempt.status = "interrupted" if reason == "worker_lost" and status != "cancelled" else status
            attempt.finished_at, attempt.stop_reason = func.now(), reason
            attempt.cleanup_done, attempt.exit_code, attempt.report = True, exit_code, report
            run.lease_until, run.worker_id = None, None
            if retry:
                run.status, run.active_attempt_id = "queued", None
                run.error_message = "Previous Worker was lost; retrying after cleanup"
            else:
                run.status, run.finished_at = status, func.now()
                run.exit_code, run.report = exit_code, report
                run.report_attempt_id = attempt_id
                run.error_message = error_message
                for name in ["total", "passed", "failed", "error", "skipped"]:
                    setattr(run, f"{name}_count", None if report is None else report["counts"][name])
        return True
