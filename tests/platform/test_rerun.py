"""重新执行创建新任务，重放返回原任务；导出已有证据，不补造报告。"""
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from probe.platform.case_catalog import CatalogUnavailable, catalog
from probe.platform.main import create_app
from probe.platform.models import TestRun as RunRecord
from probe.platform.repository import RunRepository
from tests.platform.test_runs import wait_for_terminal


pytestmark = pytest.mark.postgres


def test_rerun_preserves_scope_and_source_result_with_idempotent_replay(platform_client, platform_database, monkeypatch):
    ids = ["tests/test_contract.py::test_quantity_is_a_positive_integer[0]", "tests/test_contract.py::test_quantity_is_a_positive_integer[-1]"]
    accepted = platform_client.post("/api/runs", json={"suite_id": "contract", "timeout_seconds": 45, "case_ids": ids})
    assert accepted.status_code == 202
    original = wait_for_terminal(platform_client, accepted.json()["id"])
    assert original["status"] == "passed" and original["total_count"] == 2
    headers = {"Idempotency-Key": "rerun-test"}
    new = platform_client.post(f"/api/runs/{original['id']}/rerun", headers=headers)
    assert new.status_code == 202
    child = new.json()
    assert child["id"] != original["id"] and child["rerun_of_id"] == original["id"]
    assert child["timeout_seconds"] == 45 and child["total_count"] is None
    assert {case["id"] for case in child["case_selection"]["cases"]} == set(ids)
    result = wait_for_terminal(platform_client, child["id"])
    assert result["status"] == "passed" and result["total_count"] == 2
    assert {case["case_id"] for case in result["report"]["cases"]} == set(ids)
    assert platform_client.get(f"/api/runs/{original['id']}").json() == original
    assert len(result["attempts"]) == len(original["attempts"]) == 1

    def unavailable(*args, **kwargs):
        raise CatalogUnavailable("Controlled catalog outage")
    monkeypatch.setattr(catalog, "selection", unavailable)
    replay = platform_client.post(f"/api/runs/{original['id']}/rerun", headers=headers)
    assert replay.status_code == 200 and replay.json()["id"] == result["id"]
    wrong_source = platform_client.post(f"/api/runs/{result['id']}/rerun", headers=headers)
    assert wrong_source.status_code == 409
    wrong_kind = platform_client.post("/api/runs", json={"suite_id": "contract", "timeout_seconds": 45, "case_ids": ids}, headers=headers)
    assert wrong_kind.status_code == 409
    with platform_database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 2
        assert session.get(RunRecord, UUID(result["id"])).rerun_of_id == UUID(original["id"])


def test_rerun_rejects_active_missing_and_extra_parameters(platform_database, platform_settings):
    source = RunRepository(platform_database).create("contract")
    with TestClient(create_app(platform_settings, platform_database)) as client:
        assert client.post(f"/api/runs/{source.id}/rerun").status_code == 409
        assert client.post(f"/api/runs/{uuid4()}/rerun").status_code == 404
        assert client.post(f"/api/runs/{source.id}/rerun", json={"timeout_seconds": 1}).status_code == 422
        assert client.get(f"/api/runs/{source.id}/evidence").status_code == 409
    with platform_database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 1


def test_legacy_rerun_uses_current_whole_suite_without_inventing_old_selection(platform_database, platform_settings):
    repo = RunRepository(platform_database)
    old = repo.create("contract")
    repo.cancel(old.id)
    # 先准备已结束的旧任务，再启动 Worker，避免它在准备期间抢先领取。
    from probe.platform.runner import Worker
    worker = Worker(repo, platform_settings)
    with TestClient(create_app(platform_settings, platform_database, worker=worker)) as client:
        source_before = client.get(f"/api/runs/{old.id}").json()
        assert source_before["case_selection"] is None
        accepted = client.post(f"/api/runs/{old.id}/rerun")
        assert accepted.status_code == 202
        assert len(accepted.json()["case_selection"]["cases"]) == 6
        finished = wait_for_terminal(client, accepted.json()["id"])
        assert finished["status"] == "passed" and finished["total_count"] == 6
        assert client.get(f"/api/runs/{old.id}").json() == source_before


def test_missing_previous_case_rejects_rerun_without_expanding_scope(platform_database, platform_settings):
    selection = catalog.selection("contract", ["tests/test_contract.py::test_health_request_id_and_fault_disabled"])
    selection["cases"][0]["id"] += "_deleted"
    source = RunRepository(platform_database).create("contract", case_selection=selection)
    RunRepository(platform_database).cancel(source.id)
    with TestClient(create_app(platform_settings, platform_database)) as client:
        assert client.post(f"/api/runs/{source.id}/rerun").status_code == 422
    with platform_database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 1


def test_export_contains_actual_failed_report_http_database_and_log(platform_client, platform_database):
    accepted = platform_client.post("/api/runs", json={"suite_id": "amount_bug_demo"})
    assert accepted.status_code == 202
    run = wait_for_terminal(platform_client, accepted.json()["id"])
    assert run["status"] == "failed"
    response = platform_client.get(f"/api/runs/{run['id']}/evidence")
    assert response.status_code == 200
    assert f"probe-{run['id']}-evidence.json" in response.headers["content-disposition"]
    assert response.headers["X-Request-ID"]
    exported = response.json()
    assert exported["schema_version"] == 1 and exported["exported_at"]
    assert exported["run"]["report"] == run["report"]
    assert exported["run"]["case_selection"] == run["case_selection"]
    evidence = exported["run"]["report"]["cases"][0]["evidence"][0]
    assert evidence["response"]["status_code"] == 201
    assert evidence["response"]["body"]["total_amount_cents"] == 1990
    assert evidence["database"]["after"]["orders"][0]["total_amount_cents"] == 1990
    assert "1 failed" in exported["log"]["content"]
    assert "idempotency_key" not in exported["run"]
    with platform_database.sessions() as session:
        assert session.get(RunRecord, UUID(run["id"])).report == run["report"]


def test_export_without_report_keeps_null_results(platform_database, platform_settings):
    repo = RunRepository(platform_database)
    source = repo.create("contract")
    repo.cancel(source.id)
    with TestClient(create_app(platform_settings, platform_database)) as client:
        response = client.get(f"/api/runs/{source.id}/evidence")
        assert response.status_code == 200
        data = response.json()
        assert data["run"]["report"] is None and data["run"]["total_count"] is None
        assert data["log"] == {"content": "", "truncated": False}
        assert client.get(f"/api/runs/{uuid4()}/evidence").status_code == 404
