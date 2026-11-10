# 平台可靠性的 PostgreSQL + 进程集成测试。
# 有些用例通过 HTTP 操作任务，有些直接验证 repository／迁移，并非全是接口测试。
# SIGKILL 场景只终止本用例创建的 Worker，不会终止实际平台的 Worker。
# 建议在理解 test_runs.py 的普通任务流程后，再读超时、隔离与异常恢复。

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import psutil
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update

from probe.db import Database
from probe.platform.config import ROOT
from probe.platform.main import create_app
from probe.platform.manage import migrate
from probe.platform.models import TestRun as RunRecord
from probe.platform.repository import RunRepository


pytestmark = pytest.mark.postgres


def wait_until(check, seconds=30):
    # check 是一个检查函数：条件成立时返回结果，否则继续观察。
    # 这里只轮询状态／进程，不重新提交任务，也不把失败用例重跑成通过。
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.05)
    pytest.fail("Expected task/process condition was not observed before the deadline")


def detail(client, run_id):
    # HTTP 查询同一任务的当前状态，顺便断言任务存在且接口正常。
    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200
    return response.json()


def finished(client, run_id):
    # cancelled 和 timed_out 也是“执行已结束”，但不等同于测试通过。
    def check():
        run = detail(client, run_id)
        return run if run["status"] in {"passed", "failed", "error", "timed_out", "cancelled"} else None
    return wait_until(check)


def submit(client, suite="contract", timeout=60, key=None):
    # key 对应一次逻辑提交；重复使用同键的场景在专门的去重用例里检查。
    headers = {} if key is None else {"Idempotency-Key": key}
    response = client.post("/api/runs", json={"suite_id": suite, "timeout_seconds": timeout}, headers=headers)
    assert response.status_code == 202, response.text
    return response.json()["id"]


def alive(pid):
    # 僵尸进程已停止执行，不算仍在运行；PID 已被回收时也返回 False。
    try:
        return psutil.Process(pid).is_running() and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def child_pid(client, run_id, settings):
    # slow_demo 在自己的工作目录写 descendant.pid。先观察到它，才能证明
    # 后面的取消／超时确实停止了一个真实子进程，而非仅修改任务状态。
    def check():
        run = detail(client, run_id)
        if not run["active_attempt_id"]:
            return None
        marker = settings.artifacts_dir / run_id / run["active_attempt_id"] / "work" / "descendant.pid"
        return int(marker.read_text()) if marker.exists() else None
    return wait_until(check)


@contextmanager
def worker_process(settings, name):
    # 启动真实的独立 Worker，将数据库与报告路径指向本用例的隔离资源。
    # @contextmanager 的 yield 交出进程；退出 with 后执行 finally 负责收尾。
    environment = os.environ.copy()
    environment.update({
        "PROBE_PLATFORM_DATABASE_URL": settings.database_url,
        "PROBE_PLATFORM_DATABASE_SCHEMA": settings.database_schema,
        "PROBE_PLATFORM_ARTIFACTS_DIR": str(settings.artifacts_dir),
        "PROBE_PLATFORM_WORKER_LEASE_SECONDS": str(settings.worker_lease_seconds),
        "PROBE_PLATFORM_HEARTBEAT_SECONDS": str(settings.heartbeat_seconds),
        "PROBE_PLATFORM_POLL_SECONDS": str(settings.poll_seconds),
        "PROBE_PLATFORM_MAX_ATTEMPTS": str(settings.max_attempts),
        "TEST_DATABASE_URL": settings.order_test_database_url,
        "PLATFORM_TEST_DATABASE_URL": settings.platform_test_database_url,
    })
    environment.pop("PROBE_ATTEMPT_ID", None)
    # Worker 本身不属于某个执行尝试；它启动的测试进程才会被赋予 attempt ID。
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    with (settings.artifacts_dir / f"{name}.log").open("wb") as output:
        process = subprocess.Popen([sys.executable, "-m", "probe.platform.runner"], cwd=ROOT, env=environment,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        yield process
    finally:
        # 即使用例断言失败，也尝试停止自己启动的 Worker，避免测试污染后续用例。
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def test_queued_task_survives_api_restart(platform_database, platform_settings):
    # 先只启动 API，不启动 Worker：任务必须留在数据库中的 queued 状态。
    with TestClient(create_app(platform_settings, platform_database)) as client:
        run_id = submit(client)
        assert detail(client, run_id)["status"] == "queued"
    with TestClient(create_app(platform_settings, platform_database)) as restarted:
        # 换一个测试 API 应用，再启动 Worker，验证任务不依赖原应用的内存队列。
        assert detail(restarted, run_id)["attempt_count"] == 0
        with worker_process(platform_settings, "queue-worker"):
            assert finished(restarted, run_id)["status"] == "passed"


def test_concurrent_idempotent_submission_and_content_conflict(platform_database, platform_settings):
    # 6 个请求携带同一个提交键和内容；数据库唯一约束应只允许新增一个任务。
    with TestClient(create_app(platform_settings, platform_database)) as client:
        barrier = Barrier(6)
        def post(_):
            barrier.wait(timeout=5)
            return client.post("/api/runs", json={"suite_id": "contract", "timeout_seconds": 60}, headers={"Idempotency-Key": "stable-001"})
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(post, range(6)))
        assert [response.status_code for response in responses].count(202) == 1
        assert [response.status_code for response in responses].count(200) == 5
        assert len({response.json()["id"] for response in responses}) == 1
        # 200 表示复用原任务；202 表示首次新增。相同键改参数应返回 409。
        for payload in [{"suite_id": "contract", "timeout_seconds": 61}, {"suite_id": "failure_demo", "timeout_seconds": 60}]:
            assert client.post("/api/runs", json=payload, headers={"Idempotency-Key": "stable-001"}).status_code == 409
        with platform_database.sessions() as session:
            assert session.scalar(select(func.count()).select_from(RunRecord)) == 1


def test_queued_cancel_and_replay_are_idempotent(platform_database, platform_settings):
    # 没有 Worker 领取时直接取消：没有 started_at，也不应虚构测试结果。
    with TestClient(create_app(platform_settings, platform_database)) as client:
        run_id = submit(client, key="cancel-queued")
        first = client.post(f"/api/runs/{run_id}/cancel")
        second = client.post(f"/api/runs/{run_id}/cancel")
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert first.json()["status"] == "cancelled" and first.json()["attempt_count"] == 0
        assert first.json()["started_at"] is None and first.json()["total_count"] is None
        replay = client.post("/api/runs", json={"suite_id": "contract", "timeout_seconds": 60}, headers={"Idempotency-Key": "cancel-queued"})
        # 取消后同键重放仍返回原记录，不应意外重新排队执行。
        assert replay.status_code == 200 and replay.json()["id"] == run_id and replay.json()["status"] == "cancelled"
        assert client.get(f"/api/runs/{run_id}/junit").status_code == 409


def test_running_cancel_stops_real_descendant(platform_client, platform_settings):
    # 给 30 秒执行预算，先等待真实子进程启动，再主动取消，排除超时干扰。
    run_id = submit(platform_client, "slow_demo", 30)
    pid = child_pid(platform_client, run_id, platform_settings)
    assert alive(pid)
    response = platform_client.post(f"/api/runs/{run_id}/cancel")
    assert response.status_code == 200 and response.json()["status"] == "cancelling"
    run = finished(platform_client, run_id)
    # cancelled 只有在进程／测试 schema 清理后才保存；同时直接检查子进程已停止。
    assert run["status"] == "cancelled" and run["attempt_count"] == 1
    assert run["attempts"][0]["cleanup_done"] is True
    assert not alive(pid)
    again = platform_client.post(f"/api/runs/{run_id}/cancel")
    assert again.status_code == 200 and again.json()["finished_at"] == run["finished_at"]


def test_timeout_stops_real_descendant_and_does_not_retry(platform_client, platform_settings):
    # slow_demo 主动等待 8 秒，预算只有 3 秒，故应以 timed_out 结束且不重试。
    run_id = submit(platform_client, "slow_demo", 3)
    pid = child_pid(platform_client, run_id, platform_settings)
    run = finished(platform_client, run_id)
    assert run["status"] == "timed_out" and run["attempt_count"] == 1
    assert run["attempts"][0]["stop_reason"] == "timed_out" and run["attempts"][0]["cleanup_done"]
    assert not alive(pid)
    assert run["report"] is None and run["total_count"] is None
    # 演示执行中留下未完成 XML：不能解析为报告，也不能提供为有效下载。
    incomplete = platform_settings.artifacts_dir / run_id / run["active_attempt_id"] / "junit.xml"
    assert incomplete.read_text() == "<testsuites>"
    assert platform_client.get(f"/api/runs/{run_id}/junit").status_code == 409
    assert platform_client.post(f"/api/runs/{run_id}/cancel").status_code == 409


@pytest.mark.parametrize("timeout", [0, True, 301])
def test_invalid_timeout_is_rejected(platform_client, timeout):
    # 只替换超时字段；零、布尔值、超过 300 秒分别违反参数规则。
    response = platform_client.post("/api/runs", json={"suite_id": "contract", "timeout_seconds": timeout})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "timeout_seconds"]


def test_two_workers_use_distinct_schemas_and_directories(platform_database, platform_settings):
    # 启动两个真实 Worker 后提交两个任务，验证数据库协调领取和执行隔离。
    with TestClient(create_app(platform_settings, platform_database)) as client:
        with worker_process(platform_settings, "isolation-a"), worker_process(platform_settings, "isolation-b"):
            wait_until(lambda: client.get("/api/system").json()["online_workers"] == 2)
            ids = [submit(client, "isolation"), submit(client, "isolation")]
            runs = [finished(client, run_id) for run_id in ids]
            assert all(run["status"] == "passed" and run["total_count"] == 1 for run in runs)
            attempts = [run["attempts"][0] for run in runs]
            # 不只要求任务通过，还要求不同 Worker、不同 schema，防止共享测试数据。
            assert len({attempt["worker_id"] for attempt in attempts}) == 2
            assert len({attempt["isolation_schema"] for attempt in attempts}) == 2
            assert all(attempt["cleanup_done"] for attempt in attempts)
            for run in runs:
                # 工作目录按任务／尝试分别保留为证据；cleanup_done 不表示删除报告文件。
                assert (platform_settings.artifacts_dir / run["id"] / run["active_attempt_id"] / "work").is_dir()
            db = Database(platform_settings.order_test_database_url)
            with db.engine.connect() as connection:
                # 直接检查 PostgreSQL 命名空间，确认临时 schema 已真实删除。
                for attempt in attempts:
                    assert connection.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname=:name"), {"name": attempt["isolation_schema"]}) == 0
            db.close()


def test_sigkill_worker_is_recovered_with_new_attempt(platform_database, platform_settings):
    # 在测试执行进程已创建、任务尚在运行时，强制终止本用例的第一台 Worker。
    with TestClient(create_app(platform_settings, platform_database)) as client:
        with worker_process(platform_settings, "killed-worker") as first:
            run_id = submit(client, "stage1")
            def started():
                run = detail(client, run_id)
                return run if run["attempts"] and run["attempts"][0]["process_id"] else None
            old = wait_until(started)
            first.kill(); first.wait(timeout=3)
            # 新 Worker 等租约过期后接管：保留原任务 ID，创建新的执行尝试。
            with worker_process(platform_settings, "replacement-worker"):
                run = finished(client, run_id)
            assert run["status"] == "passed" and run["attempt_count"] == 2
            assert run["passed_count"] == 20
            assert [attempt["status"] for attempt in run["attempts"]] == ["interrupted", "passed"]
            # 旧尝试作为中断证据保留；最终报告必须属于第二次执行，不能被旧结果覆盖。
            assert all(attempt["cleanup_done"] for attempt in run["attempts"])
            assert run["attempts"][0]["id"] == old["active_attempt_id"]
            assert run["report_attempt_id"] == run["attempts"][1]["id"]
            assert not alive(old["attempts"][0]["process_id"])
            # 除了数据库的清理标记，也核验旧进程停止、两次 schema 均已删除。
            db = Database(platform_settings.order_test_database_url)
            with db.engine.connect() as connection:
                for attempt in run["attempts"]:
                    assert connection.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname=:name"), {"name": attempt["isolation_schema"]}) == 0
            db.close()


def test_repeated_worker_loss_stops_at_attempt_limit(platform_database, platform_settings):
    # 第 1、2 次尝试都强制终止 Worker，再由第三台 Worker处理恢复。
    # max_attempts=2 意味着不能无限重试；达到上限后应明确 error。
    with TestClient(create_app(platform_settings, platform_database)) as client:
        run_id = submit(client, "slow_demo", 30)
        for number in [1, 2]:
            with worker_process(platform_settings, f"lost-{number}") as process:
                def claimed():
                    run = detail(client, run_id)
                    return run if run["attempt_count"] == number and run["attempts"][-1]["process_id"] else None
                wait_until(claimed)
                process.kill(); process.wait(timeout=3)
        with worker_process(platform_settings, "limit-recovery"):
            run = finished(client, run_id)
        assert run["status"] == "error" and run["attempt_count"] == run["max_attempts"] == 2
        assert len(run["attempts"]) == 2 and all(attempt["cleanup_done"] for attempt in run["attempts"])


def test_old_attempt_cannot_write_after_lease_recovery(platform_database):
    # 这条直接验证 repository 的数据库事务，不通过 HTTP，也不启动 pytest。
    # a、b 是模拟职责的 Worker ID；通过修改测试行租约构造确定性的过期场景。
    repository = RunRepository(platform_database)
    a, b = uuid4(), uuid4()
    repository.register_worker(a, os.getpid()); repository.register_worker(b, os.getpid())
    run = repository.create("contract")
    old = repository.claim(a, 10, set())
    with platform_database.sessions.begin() as session:
        session.execute(update(RunRecord).where(RunRecord.id == run.id).values(lease_until=func.now()))
    recovering = repository.claim_recovery(b, 10)
    # b 接管恢复职责后，a 已无权提交结果，即便它声称“测试通过”。
    assert recovering.id == run.id
    assert repository.finish(run.id, old.active_attempt_id, a, status="passed", exit_code=0, report=None) is False
    assert repository.finish(run.id, old.active_attempt_id, b, status="error", exit_code=None, report=None, reason="worker_lost", recovery=True)
    current = repository.claim(b, 10, set())
    # 新尝试 ID 是第二层保护：旧尝试再次写回也必须拒绝，当前仍应为 running。
    assert current.active_attempt_id != old.active_attempt_id
    assert repository.finish(run.id, old.active_attempt_id, a, status="passed", exit_code=0, report=None) is False
    assert repository.get(run.id).status == "running" and repository.get(run.id).report is None


def test_legacy_migration_preserves_old_report(platform_admin):
    # 在独立 schema 中重建第二阶段旧结构，然后验证 Alembic 能识别并升级。
    # 这测试结构迁移，不是执行接口测试；固定旧数据用于检查迁移前后是否保持一致。
    schema = "platform_case_" + uuid4().hex
    with platform_admin.engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    database = Database(platform_admin.engine.url.render_as_string(hide_password=False), schema=schema)
    try:
        configuration = Config(str(ROOT / "alembic.ini"))
        with database.engine.connect() as connection:
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, "0001_stage2")
            # 模拟第二阶段 create_all 建表的情况：有业务表，但没有 Alembic 版本表。
            connection.execute(text("DROP TABLE alembic_version"))
            legacy_id = uuid4()
            # 6 是旧记录夹具中的计数，不表示本轮执行了 6 条测试，不会写入实际平台库。
            connection.execute(text("INSERT INTO test_runs(id,suite_id,status,total_count,passed_count,report) VALUES(:id,'contract','passed',6,6,'{\"evidence\":\"legacy-report-kept\"}'::jsonb)"), {"id": legacy_id})
            connection.commit()
        migrate(database); migrate(database)
        # 重复迁移应安全，保留旧 ID、状态、计数和报告，仅补新字段与版本信息。
        with database.engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM test_runs WHERE id=:id"), {"id": legacy_id}).mappings().one()
            assert row["status"] == "passed" and row["passed_count"] == 6
            assert row["report"] == {"evidence": "legacy-report-kept"}
            assert row["case_selection"] is None  # 旧任务没有用例选择，不补造历史。
            assert row["idempotency_key"] == "legacy-" + str(legacy_id) and row["attempt_count"] == 0
            assert row["rerun_of_id"] is None
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004_rerun_source"
    finally:
        # 连旧结构、版本表一起清理，确保迁移实验不会影响实际平台历史记录。
        database.close()
        with platform_admin.engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
