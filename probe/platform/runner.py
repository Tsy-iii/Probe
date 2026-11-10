import asyncio
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import text

from probe.db import Database
from probe.platform.config import ROOT, PlatformSettings
from probe.platform.processes import read_stop_reason, terminate_attempt, write_stop_reason
from probe.platform.reports import classify_result, parse_junit
from probe.platform.case_catalog import belongs_to_suite
from probe.platform.repository import RunRepository
from probe.http_testing import finalize_services


logger = logging.getLogger("probe.worker")


from probe.platform.suites import SUITES, Suite


class Worker:
    def __init__(self, repository: RunRepository, settings: PlatformSettings):
        self.repository, self.settings = repository, settings
        self.id = uuid4()
        self.stop = asyncio.Event()
        self.active: tuple[UUID, UUID] | None = None
        self.lease_lost = False
        self.task: asyncio.Task | None = None
        self.pulse: asyncio.Task | None = None

    async def start(self):
        if os.name != "posix":
            raise RuntimeError("Stage 3 process cleanup currently supports macOS/Linux")
        self.settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self.repository.register_worker, self.id, os.getpid())
        self.pulse = asyncio.create_task(self._heartbeat())
        self.task = asyncio.create_task(self._consume())

    async def close(self):
        self.stop.set()
        if self.task:
            await self.task
        if self.pulse:
            await self.pulse

    async def _heartbeat(self):
        while not self.stop.is_set():
            try:
                active = self.active
                owned = await asyncio.to_thread(self.repository.heartbeat, self.id, self.settings.worker_lease_seconds, active)
                if not owned and self.active == active:
                    self.lease_lost = True
            except Exception as exc:
                logger.error("worker_id=%s heartbeat_error=%s", self.id, type(exc).__name__)
            await asyncio.sleep(self.settings.heartbeat_seconds)

    async def _consume(self):
        required = {suite.id for suite in SUITES.values() if suite.requires_postgres}
        try:
            while not self.stop.is_set():
                try:
                    recovered = await asyncio.to_thread(self.repository.claim_recovery, self.id, self.settings.worker_lease_seconds)
                    run = recovered or await asyncio.to_thread(self.repository.claim, self.id, self.settings.worker_lease_seconds, required)
                    if run:
                        self.active, self.lease_lost = (run.id, run.active_attempt_id), False
                        if recovered:
                            await self._recover(run)
                        else:
                            await self._execute(run)
                    else:
                        await asyncio.sleep(self.settings.poll_seconds)
                except Exception as exc:
                    logger.error("worker_id=%s run_id=%s worker_error=%s", self.id, self.active, type(exc).__name__)
                    await asyncio.sleep(self.settings.poll_seconds)
                finally:
                    self.active = None
        finally:
            try:
                await asyncio.to_thread(self.repository.offline, self.id)
            except Exception:
                pass

    def directory(self, run_id: UUID, attempt_id: UUID) -> Path:
        return self.settings.artifacts_dir / str(run_id) / str(attempt_id)

    def _schema(self, schema: str, create: bool):
        if not re.fullmatch(r"run_[a-f0-9]{32}", schema):
            raise ValueError("Invalid execution schema")
        database = Database(self.settings.order_test_database_url)
        try:
            with database.engine.begin() as connection:
                query = f'CREATE SCHEMA "{schema}"' if create else f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'
                connection.execute(text(query))
        finally:
            database.close()

    def _platform_test_schema(self, attempt_id, create):
        schema = "platform_case_" + attempt_id.hex
        database = Database(self.settings.platform_test_database_url)
        try:
            with database.engine.begin() as connection:
                query = f'CREATE SCHEMA "{schema}"' if create else f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'
                connection.execute(text(query))
        finally:
            database.close()
        return schema

    async def _cleanup(self, attempt, run_id):
        await asyncio.to_thread(terminate_attempt, attempt.id)
        await asyncio.to_thread(finalize_services, self.directory(run_id, attempt.id) / "http-services.json")
        if attempt.isolation_schema:
            await asyncio.to_thread(self._schema, attempt.isolation_schema, False)
        # 标记在 DDL 前落盘；Worker 异常恢复也能发现并清理附属测试空间。
        marker = self.directory(run_id, attempt.id) / "platform-test-schema"
        if marker.exists():
            if marker.read_text() != "platform_case_" + attempt.id.hex:
                raise ValueError("Platform test schema ownership marker does not match the attempt")
            await asyncio.to_thread(self._platform_test_schema, attempt.id, False)

    async def _spawn(self, command, *, environment, directory, output):
        return await asyncio.create_subprocess_exec(*command, cwd=directory / "work", env=environment,
                                                  stdout=output, stderr=asyncio.subprocess.STDOUT, start_new_session=True)

    async def _execute(self, run):
        attempt = run.attempts[-1]
        directory = self.directory(run.id, attempt.id)
        suite = SUITES[run.suite_id]
        process, exit_code, report, reason = None, None, None, None
        status, message = "error", None
        started = time.monotonic()
        logger.info("worker_id=%s run_id=%s attempt_id=%s started", self.id, run.id, attempt.id)
        try:
            (directory / "work").mkdir(parents=True, exist_ok=False)
            if attempt.isolation_schema:
                await asyncio.to_thread(self._schema, attempt.isolation_schema, True)
            platform_test_schema = None
            if suite.requires_platform_test:
                platform_test_schema = "platform_case_" + attempt.id.hex
                (directory / "platform-test-schema").write_text(platform_test_schema)
                await asyncio.to_thread(self._platform_test_schema, attempt.id, True)
            environment = os.environ.copy()
            environment.update({
                "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1", "APP_ENV": "test", "FAULT_INJECTION_ENABLED": "false",
                "TEST_DATABASE_URL": self.settings.order_test_database_url,
                "PLATFORM_TEST_DATABASE_URL": self.settings.platform_test_database_url,
                "PROBE_PLATFORM_DATABASE_URL": self.settings.database_url,
                "PROBE_PLATFORM_ARTIFACTS_DIR": str(self.settings.artifacts_dir),
                "PROBE_PLATFORM_WORKER_LEASE_SECONDS": str(self.settings.worker_lease_seconds),
                "PROBE_PLATFORM_HEARTBEAT_SECONDS": str(self.settings.heartbeat_seconds),
                "PROBE_ATTEMPT_ID": str(attempt.id),
                "PROBE_HTTP_SERVICE_MANIFEST": str(directory / "http-services.json"),
            })
            if self.repository.database.schema:
                environment["PROBE_PLATFORM_DATABASE_SCHEMA"] = self.repository.database.schema
            else:
                environment.pop("PROBE_PLATFORM_DATABASE_SCHEMA", None)
            if attempt.isolation_schema:
                environment["PROBE_TEST_SCHEMA"] = attempt.isolation_schema
            else:
                environment.pop("PROBE_TEST_SCHEMA", None)
            if platform_test_schema:
                environment["PROBE_PLATFORM_TEST_SCHEMA"] = platform_test_schema
            else:
                environment.pop("PROBE_PLATFORM_TEST_SCHEMA", None)
            targets = suite.targets
            if run.case_selection is not None:
                targets = [case["id"] for case in run.case_selection["cases"]]
                if not targets or len(targets) != len(set(targets)) or not all(belongs_to_suite(target, suite) for target in targets):
                    raise ValueError("Invalid saved case selection")
            # 对全选也使用提交时保存的精确 ID；新增用例不会混入已经排队的任务。
            pytest_args = [str(ROOT / target) for target in targets]
            pytest_args += ["-q", "-c", str(ROOT / "pytest.ini"), "--rootdir", str(ROOT), "-o", "addopts=", "-p", "no:cacheprovider",
                            "-p", "probe.platform.collection", f"--basetemp={directory / 'tmp'}", f"--junitxml={directory / 'junit.xml'}"]
            if suite.requires_postgres:
                pytest_args.append("--require-postgres")
            command = [sys.executable, "-m", "probe.platform.execution", "--run-id", str(run.id), "--attempt-id", str(attempt.id),
                       "--worker-id", str(self.id), "--parent-pid", str(os.getpid()), "--timeout", str(run.timeout_seconds),
                       "--directory", str(directory), *pytest_args]
            with (directory / "output.log").open("wb") as output:
                process = await self._spawn(command, environment=environment, directory=directory, output=output)
                await asyncio.to_thread(self.repository.set_process, run.id, attempt.id, self.id, process.pid)
                last_contact = time.monotonic()
                while process.returncode is None:
                    if self.stop.is_set() or self.lease_lost:
                        reason = "worker_lost"
                    elif time.monotonic() - started >= run.timeout_seconds:
                        reason = "timed_out"
                    else:
                        try:
                            control = await asyncio.to_thread(self.repository.control, run.id, attempt.id, self.id)
                            last_contact = time.monotonic()
                            if not control["owned"]:
                                reason = "worker_lost"
                            elif control["cancelled"]:
                                reason = "cancelled"
                        except Exception:
                            if time.monotonic() - last_contact >= self.settings.worker_lease_seconds:
                                reason = "worker_lost"
                    if reason:
                        write_stop_reason(directory, reason)
                        await asyncio.to_thread(terminate_attempt, attempt.id)
                        # During early interpreter startup the environment tag
                        # may not be visible yet; the live Process object is safe.
                        if process.returncode is None:
                            try:
                                process.kill()
                            except ProcessLookupError:
                                pass
                        break
                    await asyncio.sleep(0.1)
                exit_code = await process.wait()
            reason = reason or read_stop_reason(directory)
            path = directory / "junit.xml"
            if path.exists():
                try:
                    report = await asyncio.to_thread(parse_junit, path)
                except Exception as exc:
                    if reason is None:
                        raise
                    logger.warning("run_id=%s attempt_id=%s incomplete_report=%s", run.id, attempt.id, type(exc).__name__)
            if reason:
                status = reason if reason in {"cancelled", "timed_out"} else "error"
                message = f"Execution stopped: {reason}"
            elif report is not None:
                status = classify_result(exit_code, report)
                message = None if status != "error" else f"pytest exit code {exit_code}; invalid test outcome"
            else:
                message = "Execution finished without a JUnit report"
        except Exception as exc:
            status, message = "error", f"Could not execute pytest or read its report ({type(exc).__name__})"
        await self._cleanup(attempt, run.id)
        committed = await asyncio.to_thread(self.repository.finish, run.id, attempt.id, self.id, status=status,
                                           exit_code=exit_code, report=report, reason=reason, error_message=message)
        logger.info("run_id=%s attempt_id=%s status=%s committed=%s", run.id, attempt.id, status, committed)

    async def _recover(self, run):
        attempt = next(a for a in run.attempts if a.id == run.active_attempt_id)
        directory = self.directory(run.id, attempt.id)
        # Fence by changing run ownership before stopping the old execution.
        await self._cleanup(attempt, run.id)
        reason = read_stop_reason(directory) or "worker_lost"
        status = reason if reason in {"cancelled", "timed_out"} else "error"
        report = None
        path = directory / "junit.xml"
        if path.exists():
            try:
                report = await asyncio.to_thread(parse_junit, path)
            except Exception:
                pass
        await asyncio.to_thread(self.repository.finish, run.id, attempt.id, self.id, status=status, exit_code=None,
                                report=report, reason=reason, error_message=f"Recovered interrupted execution: {reason}", recovery=True)
        logger.info("worker_id=%s run_id=%s recovered_attempt=%s reason=%s", self.id, run.id, attempt.id, reason)


async def serve():
    settings = PlatformSettings()
    database = Database(settings.database_url, schema=settings.database_schema)
    worker = Worker(RunRepository(database), settings)
    loop = asyncio.get_running_loop()
    for sig in [signal.SIGTERM, signal.SIGINT]:
        loop.add_signal_handler(sig, worker.stop.set)
    try:
        await worker.start()
        logger.info("worker_id=%s process_id=%s online", worker.id, os.getpid())
        await worker.task
        await worker.close()
    finally:
        database.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(serve())
