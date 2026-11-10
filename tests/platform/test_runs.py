# 平台任务 API 的集成测试：提交、查询、报告、日志、参数校验与历史持久化。
# platform_client 会启动测试 Worker，它会实际运行 pytest 子进程。
# 这些是“测试执行平台的测试”：外层断言通过，不代表内层故障演示也通过。

import asyncio
import time
from datetime import datetime
from uuid import UUID, uuid4
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from probe.platform.main import create_app
from probe.platform.models import TestRun as RunRecord
# RunRecord 是任务表模型的别名，避免 pytest 把名字以 Test 开头的模型当测试类。
from probe.platform.repository import RunRepository
from probe.platform.runner import Worker


pytestmark = pytest.mark.postgres


def submit(client, suite="contract"):
    # 默认提交无数据库的契约套件。HTTP 202 只表示任务接收成功。
    response = client.post("/api/runs", json={"suite_id": suite}, headers={"X-Request-ID": "platform-test-001"})
    assert response.status_code == 202, response.text
    assert response.headers["X-Request-ID"] == "platform-test-001"
    run = response.json()
    assert run["status"] == "queued"
    # queued 阶段尚未执行，结果未知；不能提前填“通过 0 条”冒充实际结果。
    assert run["total_count"] is None
    return run["id"]


def wait_for_terminal(client, run_id):
    # 观察同一个任务的状态，直到结束；不会重新提交或重跑失败任务。
    # 30 秒是这条测试的等待上限，与任务本身配置的执行超时是两个概念。
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        run = response.json()
        if run["status"] in {"passed", "failed", "error"}:
            return run
        time.sleep(0.05)
    pytest.fail("Run did not reach a terminal state within the test observation window")


def test_suite_registry_and_empty_history(platform_client):
    # 验证白名单套件和初始空历史；这里读取的是本用例的新 schema。
    assert platform_client.get("/health").status_code == 200
    assert platform_client.get("/health/ready").status_code == 200
    suites = platform_client.get("/api/suites").json()
    assert {s["id"] for s in suites} == {"stage1", "contract", "failure_demo", "slow_demo", "isolation", "stock_boundary", "amount_bug_demo", "http_orders", "http_control", "demo_orders"}
    assert next(s for s in suites if s["id"] == "failure_demo")["expected_failure"] is True
    history = platform_client.get("/api/runs").json()
    assert history["items"] == [] and history["total"] == 0
    assert all(count == 0 for count in history["summary"].values())


def test_real_pytest_pass_persists_results_and_artifacts(platform_client, platform_database):
    # 这一个外层用例会在子进程运行 6 条契约测试，然后核对它们的实际结果。
    run_id = submit(platform_client)
    run = wait_for_terminal(platform_client, run_id)
    assert run["status"] == "passed" and run["exit_code"] == 0
    assert (run["total_count"], run["passed_count"], run["failed_count"], run["error_count"], run["skipped_count"]) == (6, 6, 0, 0, 0)
    assert datetime.fromisoformat(run["created_at"]) <= datetime.fromisoformat(run["started_at"]) <= datetime.fromisoformat(run["finished_at"])
    assert len(run["report"]["cases"]) == 6
    # 同时核对三个结果载体：结构化报告、原始 stdout 日志、可下载的 JUnit。
    output = platform_client.get(f"/api/runs/{run_id}/log").json()
    assert "6 passed" in output["content"] and output["truncated"] is False
    download = platform_client.get(f"/api/runs/{run_id}/junit")
    assert download.status_code == 200
    assert len(list(ElementTree.fromstring(download.content).iter("testcase"))) == 6
    with platform_database.sessions() as session:
        # API 返回正确还不够，任务表中的状态、计数和 JSONB 报告也应一致。
        saved = session.get(RunRecord, UUID(run_id))
        assert saved.status == "passed" and saved.passed_count == 6 and saved.report["counts"]["total"] == 6
    history = platform_client.get("/api/runs").json()
    assert history["total"] == 1 and history["summary"]["passed"] == 1


def test_expected_failure_remains_failed_with_real_evidence(platform_client, platform_database):
    # 内层演示故意用 201 的断言检查固定 503，因此内层应 failed、退出码 1。
    # 外层验证平台如实保存这个失败；平台不能把“预期失败”改成 passed。
    run_id = submit(platform_client, "failure_demo")
    run = wait_for_terminal(platform_client, run_id)
    assert run["status"] == "failed" and run["exit_code"] == 1
    assert (run["total_count"], run["passed_count"], run["failed_count"], run["error_count"]) == (1, 0, 1, 0)
    details = run["report"]["cases"][0]["details"]
    # 失败证据应含期望值、实际值、业务错误码和可关联日志的请求 ID。
    assert "Expected 201" in details and "503" in details and "injected_error" in details
    assert "failure-demo-001" in details
    assert "1 failed" in platform_client.get(f"/api/runs/{run_id}/log").json()["content"]
    with platform_database.sessions() as session:
        assert session.get(RunRecord, UUID(run_id)).status == "failed"


def test_invalid_suite_and_extra_fields_create_no_run(platform_client, platform_database):
    # 调用者只能选择白名单套件，不能传任意文件路径或 shell 命令。
    invalid = platform_client.post("/api/runs", json={"suite_id": "../../arbitrary.py"})
    assert invalid.status_code == 422
    extra = platform_client.post("/api/runs", json={"suite_id": "contract", "command": "echo unsafe"})
    assert extra.status_code == 422
    with platform_database.sessions() as session:
        # 参数被拒绝后，数据库不应留下一个无法执行的任务。
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 0


def test_unknown_runs_and_invalid_limits_are_explicit(platform_client):
    # UUID 合法但任务不存在时是 404；UUID 或分页参数格式非法时是 422。
    run_id = uuid4()
    for suffix in ["", "/log", "/junit"]:
        assert platform_client.get(f"/api/runs/{run_id}{suffix}").status_code == 404
    assert platform_client.get("/api/runs/not-a-uuid").status_code == 422
    assert platform_client.get("/api/runs?limit=0").status_code == 422
    assert platform_client.get("/api/runs?limit=101").status_code == 422


def test_fifo_jobs_have_independent_reports(platform_client):
    # 本 fixture 只有一个 Worker／执行槽：先提交的正常任务结束后，才能启动演示。
    first, second = submit(platform_client), submit(platform_client, "failure_demo")
    a, b = wait_for_terminal(platform_client, first), wait_for_terminal(platform_client, second)
    assert a["status"] == "passed" and b["status"] == "failed"
    assert datetime.fromisoformat(a["finished_at"]) <= datetime.fromisoformat(b["started_at"])
    # 两个任务必须有独立 ID 和报告，不能被后一次执行覆盖。
    assert a["id"] != b["id"] and a["report"]["counts"]["total"] == 6 and b["report"]["counts"]["total"] == 1
    history = platform_client.get("/api/runs").json()
    assert history["summary"]["passed"] == history["summary"]["failed"] == 1


def test_completed_results_survive_app_restart(platform_database, platform_settings):
    # 第一个测试应用执行完后关闭；第二个使用相同测试库与文件目录读取结果。
    # 这是测试内的生命周期模拟，不会重启正在 8001 运行的实际平台服务。
    worker = Worker(RunRepository(platform_database), platform_settings)
    with TestClient(create_app(platform_settings, platform_database, worker=worker)) as client:
        run_id = submit(client)
        before = wait_for_terminal(client, run_id)
    with TestClient(create_app(platform_settings, platform_database)) as restarted:
        after = restarted.get(f"/api/runs/{run_id}").json()
        assert after == before
        assert restarted.get(f"/api/runs/{run_id}/junit").status_code == 200


def test_launch_error_is_not_a_pass_and_next_job_can_run(platform_client, monkeypatch):
    # monkeypatch 只让第一次“启动子进程”确定地抛出 OSError；数据库仍然真实。
    original = asyncio.create_subprocess_exec
    remaining_failures = 1
    async def fail_once(*args, **kwargs):
        nonlocal remaining_failures
        if remaining_failures:
            remaining_failures -= 1
            raise OSError("controlled launch failure")
        return await original(*args, **kwargs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_once)
    # 启动失败应记录 error，且没有退出码／测试计数，不能算成测试通过。
    failed = wait_for_terminal(platform_client, submit(platform_client))
    assert failed["status"] == "error" and failed["exit_code"] is None
    assert failed["total_count"] is None and failed["report"] is None
    assert "OSError" in failed["error_message"]
    assert platform_client.get(f"/api/runs/{failed['id']}/junit").status_code == 409
    # 另行提交一个新任务验证 Worker 还能工作，不把第一次失败重试后改成通过。
    next_run = wait_for_terminal(platform_client, submit(platform_client))
    assert next_run["status"] == "passed"
