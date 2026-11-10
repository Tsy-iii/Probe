"""真实 PostgreSQL 与 pytest 子进程验证：选择范围、快照与提交去重。

这些测试检查平台执行了哪些真实用例；不能只核对前端勾选数量。
每条外层用例使用独立 platform_case schema，业务测试仍使用 probe_test。
"""
from uuid import UUID
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from probe.platform.case_catalog import CatalogUnavailable, catalog
from probe.platform.main import create_app
from probe.platform.models import TestRun as RunRecord
from probe.platform.repository import RunRepository
from probe.platform.runner import Worker
from tests.platform.test_runs import wait_for_terminal


pytestmark = pytest.mark.postgres


@pytest.fixture
def selection_client(platform_settings, platform_database, database):
    # 不启动 Worker，便于精确检查拒绝请求与提交前后的持久数据。
    with TestClient(create_app(platform_settings, platform_database, order_database=database)) as client:
        yield client


def test_real_directory_keeps_parameterized_cases_separate(selection_client, platform_database, database):
    response = selection_client.get("/api/suites/stage1/cases")
    assert response.status_code == 200
    directory = response.json()
    assert directory["count"] == len(directory["cases"]) == 20
    quantity = [case for case in directory["cases"] if case["function"] == "test_quantity_is_a_positive_integer"]
    assert len(quantity) == 4
    assert {case["parameters"]["quantity"] for case in quantity} == {"0", "-1", "1.5", "True"}
    assert len({case["id"] for case in directory["cases"]}) == directory["count"]
    assert all(case["source_line"] > 0 and case["source_file"] in ["tests/test_orders.py", "tests/test_contract.py"] for case in directory["cases"])
    assert next(case for case in directory["cases"] if case["function"] == "test_create_query_and_pay")["requires_postgres"] is True
    assert all(case["requires_postgres"] is False for case in quantity)
    assert selection_client.get("/api/suites/not-a-suite/cases").status_code == 404
    with platform_database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 0
    # 读取目录不会执行目录内的业务测试，也不会清理外层测试准备的数据。
    from probe.models import Product, Order
    with database.sessions() as session:
        assert session.get(Product, 1).stock == 5
        assert session.scalar(select(func.count()).select_from(Order)) == 0


@pytest.mark.parametrize("suite,selection,expected_count", [
    ("contract", "one", 1), ("contract", "two", 2), ("contract", "all", 6),
    ("stage1", "order", 1), ("stage1", "all", 20),
])
def test_real_worker_executes_exact_saved_scope(platform_client, platform_database, suite, selection, expected_count):
    directory = platform_client.get(f"/api/suites/{suite}/cases").json()
    cases = directory["cases"]
    if selection == "order":
        chosen = [case["id"] for case in cases if case["function"] == "test_create_query_and_pay"]
    elif selection == "all":
        chosen = None
    else:
        chosen = [case["id"] for case in cases[:1 if selection == "one" else 2]]
    response = platform_client.post("/api/runs", json={"suite_id": suite, "case_ids": chosen, "catalog_revision": directory["catalog_revision"]})
    assert response.status_code == 202, response.text
    accepted = response.json()
    snapshot = accepted["case_selection"]
    assert accepted["total_count"] is None
    assert len(snapshot["cases"]) == expected_count
    assert snapshot["mode"] == ("all" if chosen is None else "selected")
    run = wait_for_terminal(platform_client, accepted["id"])
    assert run["status"] == "passed", run
    assert run["total_count"] == run["passed_count"] == expected_count
    expected_ids = {case["id"] for case in snapshot["cases"]}
    assert {case["case_id"] for case in run["report"]["cases"]} == expected_ids
    assert run["case_selection"] == snapshot
    junit = platform_client.get(f"/api/runs/{run['id']}/junit")
    assert junit.status_code == 200
    recorded_ids = {prop.get("value") for prop in ElementTree.fromstring(junit.content).iter("property") if prop.get("name") == "probe.case_id"}
    assert recorded_ids == expected_ids
    with platform_database.sessions() as session:
        saved = session.get(RunRecord, UUID(run["id"]))
        assert saved.case_selection == snapshot
        assert saved.report["counts"]["total"] == expected_count
    assert platform_client.get("/api/runs").json()["items"][0]["case_selection"] == snapshot


@pytest.mark.parametrize("ids", [
    [], ["tests/test_contract.py::test_health_request_id_and_fault_disabled"] * 2,
    ["tests/test_stock_boundary.py::test_stock_exactly_enough"],
    ["../../other.py::test_any"], ["--help"], ["tests/test_contract.py::missing"], [1],
])
def test_invalid_selection_creates_no_task(selection_client, platform_database, ids):
    response = selection_client.post("/api/runs", json={"suite_id": "contract", "case_ids": ids})
    assert response.status_code == 422
    with platform_database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 0


def test_stale_directory_is_rejected_before_saving(selection_client, platform_database):
    response = selection_client.post("/api/runs", json={"suite_id": "contract", "catalog_revision": "0" * 64})
    assert response.status_code == 422 and "Case list changed" in response.json()["detail"]
    with platform_database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 0


def test_one_suite_collection_failure_does_not_block_other_suites(selection_client, monkeypatch):
    original = catalog._fingerprint

    def broken_demo(files):
        if "examples/pytest_failure_demo.py" in files:
            raise OSError("Controlled unreadable demo file")
        return original(files)

    monkeypatch.setattr(catalog, "_fingerprint", broken_demo)
    assert selection_client.get("/api/suites/failure_demo/cases").status_code == 503
    other = selection_client.get("/api/suites/contract/cases")
    assert other.status_code == 200 and other.json()["count"] == 6


def test_replay_compares_selection_and_does_not_recollect(selection_client, platform_database, monkeypatch):
    directory = selection_client.get("/api/suites/contract/cases").json()
    ids = [case["id"] for case in directory["cases"][:2]]
    body = {"suite_id": "contract", "case_ids": ids}
    headers = {"Idempotency-Key": "selection-replay"}
    first = selection_client.post("/api/runs", json=body, headers=headers)
    assert first.status_code == 202
    replay = selection_client.post("/api/runs", json={**body, "case_ids": ids[::-1]}, headers=headers)
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]
    conflict = selection_client.post("/api/runs", json={**body, "case_ids": ids[:1]}, headers=headers)
    assert conflict.status_code == 409

    def unavailable(*args, **kwargs):
        raise CatalogUnavailable("Controlled collection failure")
    monkeypatch.setattr(catalog, "suite", unavailable)
    # 已接受的提交重放直接返回原快照，目录失效也不产生新的执行。
    replay = selection_client.post("/api/runs", json=body, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["case_selection"] == first.json()["case_selection"]
    failed_new = selection_client.post("/api/runs", json=body)
    assert failed_new.status_code == 503
    with platform_database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 1


def test_missing_saved_case_is_error_without_falling_back_to_full_suite(selection_client, platform_database, platform_settings):
    case_id = "tests/test_contract.py::test_health_request_id_and_fault_disabled"
    accepted = selection_client.post("/api/runs", json={"suite_id": "contract", "case_ids": [case_id]})
    assert accepted.status_code == 202
    # 模拟排队后函数被删除：保留有效白名单文件，但存储的具体函数已不存在。
    with platform_database.sessions.begin() as session:
        run = session.get(RunRecord, UUID(accepted.json()["id"]))
        snapshot = run.case_selection
        snapshot["cases"][0]["id"] += "_removed"
        run.case_selection = {**snapshot}
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(run, "case_selection")
    worker = Worker(RunRepository(platform_database), platform_settings)
    with TestClient(create_app(platform_settings, platform_database, worker=worker)) as client:
        run = wait_for_terminal(client, accepted.json()["id"])
        assert run["status"] == "error"
        assert run["total_count"] in [None, 0]
        assert "not found" in client.get(f"/api/runs/{run['id']}/log").json()["content"].lower()
        assert run["attempts"][0]["cleanup_done"] is True


def test_legacy_task_remains_readable_and_replayable(selection_client, platform_database):
    saved = RunRepository(platform_database).create("contract", key="legacy-selection")
    assert saved.case_selection is None
    response = selection_client.post("/api/runs", json={"suite_id": "contract"}, headers={"Idempotency-Key": "legacy-selection"})
    assert response.status_code == 200 and response.json()["id"] == str(saved.id)
    assert response.json()["case_selection"] is None
