# 通过真实平台提交新套件，检查证据在测试 schema 清理后仍可读取。
from uuid import UUID

import pytest
from sqlalchemy import text

from probe.db import Database
from probe.platform.models import TestRun as RunRecord


pytestmark = pytest.mark.postgres


def test_stock_and_amount_contrast_keep_real_evidence(platform_client, platform_database, platform_settings):
    import time

    def run_suite(suite):
        accepted = platform_client.post("/api/runs", json={"suite_id": suite})
        assert accepted.status_code == 202
        run_id = accepted.json()["id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = platform_client.get(f"/api/runs/{run_id}")
            assert result.status_code == 200
            run = result.json()
            if run["status"] in {"passed", "failed", "error"}:
                return run
            time.sleep(0.05)
        pytest.fail("New suite did not finish within the observation window")

    stock = run_suite("stock_boundary")
    assert stock["status"] == "passed" and stock["total_count"] == stock["passed_count"] == 1
    stock_case = stock["report"]["cases"][0]
    assert stock_case["source_file"] == "tests/test_stock_boundary.py"
    assert stock_case["source_line"] > 0
    first, second = stock_case["evidence"]
    assert first["response"]["status_code"] == 201 and first["response"]["body"]["total_amount_cents"] == 9950
    assert first["database"]["before"]["products"][0]["stock"] == 5
    assert first["database"]["after"]["products"][0]["stock"] == 0
    assert second["response"]["status_code"] == 409
    assert second["database"]["after"]["order_count"] == 1
    assert second["database"]["before"] == second["database"]["after"]

    comparison = run_suite("amount_bug_demo")
    assert comparison["status"] == "failed" and comparison["exit_code"] == 1
    assert (comparison["total_count"], comparison["passed_count"], comparison["failed_count"]) == (2, 1, 1)
    faulty, corrected = comparison["report"]["cases"]
    assert faulty["outcome"] == "failed" and corrected["outcome"] == "passed"
    bug = faulty["evidence"][0]
    good = corrected["evidence"][0]
    assert bug["response"]["status_code"] == 201
    assert bug["response"]["body"]["total_amount_cents"] == 1990
    assert bug["database"]["after"]["orders"][0]["total_amount_cents"] == 1990
    assert good["response"]["body"]["total_amount_cents"] == 3980
    assert good["database"]["before"]["order_count"] == 0
    assert all(check["expected"] == check["actual"] for check in good["checks"])

    # 用例结束后 schema 已删除，但请求／数据库快照存在于平台 JSONB 报告中。
    with platform_database.sessions() as session:
        saved = session.get(RunRecord, UUID(comparison["id"]))
        assert saved.report["cases"][0]["evidence"][0]["database"]["after"]["orders"][0]["total_amount_cents"] == 1990
    database = Database(platform_settings.order_test_database_url)
    with database.engine.connect() as connection:
        for run in [stock, comparison]:
            assert run["attempts"][0]["cleanup_done"]
            schema = run["attempts"][0]["isolation_schema"]
            assert connection.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname=:name"), {"name": schema}) == 0
    database.close()
    assert platform_client.get(f"/api/runs/{comparison['id']}/junit").status_code == 200
