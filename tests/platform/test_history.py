"""历史查询的真实 PostgreSQL 接口验证：分页、筛选、统计与字面量搜索。"""
from datetime import datetime, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from probe.platform.main import create_app
from probe.platform.models import TestRun as RunRecord


pytestmark = pytest.mark.postgres


@pytest.fixture
def history_client(platform_settings, platform_database):
    # 同一创建时间，用 UUID 再排序，验证分页不会在时间相同时丢行或重复。
    with platform_database.sessions.begin() as session:
        for number in range(1, 15):
            session.add(RunRecord(id=UUID(int=number), suite_id="contract" if number % 2 else "stage1",
                                  status="passed" if number <= 8 else "failed", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                  idempotency_key=f"history-{number}", request_hash="test-fingerprint", report={"not_for_list": "large-report"}))
    with TestClient(create_app(platform_settings, platform_database)) as client:
        yield client


def test_pagination_stable_order_and_global_counts(history_client, platform_database):
    first = history_client.get("/api/runs?limit=3")
    second = history_client.get("/api/runs?limit=3&offset=3")
    assert first.status_code == second.status_code == 200
    a, b = first.json(), second.json()
    assert [UUID(row["id"]).int for row in a["items"]] == [14, 13, 12]
    assert [UUID(row["id"]).int for row in b["items"]] == [11, 10, 9]
    assert a["total"] == a["matched_total"] == b["total"] == 14
    assert a["summary"]["passed"] == 8 and a["summary"]["failed"] == 6
    assert (b["limit"], b["offset"]) == (3, 3)
    assert "large-report" not in first.text and "request_hash" not in first.text
    with platform_database.sessions() as session:
        assert session.get(RunRecord, UUID(int=14)).report == {"not_for_list": "large-report"}


def test_filters_match_database_but_do_not_change_global_statistics(history_client):
    response = history_client.get("/api/runs?suite_id=contract&status=failed")
    assert response.status_code == 200
    data = response.json()
    assert {UUID(row["id"]).int for row in data["items"]} == {9, 11, 13}
    assert data["matched_total"] == 3
    assert data["total"] == 14 and data["summary"]["passed"] == 8
    named = history_client.get("/api/runs", params={"q": "订单 API"}).json()
    assert named["matched_total"] == 7 and all(row["suite_id"] == "stage1" for row in named["items"])
    searched = history_client.get("/api/runs", params={"q": str(UUID(int=13)).upper()}).json()
    assert searched["matched_total"] == 1 and searched["items"][0]["id"] == str(UUID(int=13))
    beyond = history_client.get("/api/runs?offset=1000").json()
    assert beyond["items"] == [] and beyond["matched_total"] == 14


@pytest.mark.parametrize("term", ["%", "_%", "' OR 1=1 --", "does-not-exist"])
def test_search_is_literal_and_never_modifies_data(history_client, platform_database, term):
    response = history_client.get("/api/runs", params={"q": term})
    assert response.status_code == 200
    assert response.json()["matched_total"] == 0 and response.json()["total"] == 14
    with platform_database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 14


@pytest.mark.parametrize("params", [{"limit": 0}, {"offset": -1}, {"offset": 1000001}, {"status": "unknown"}, {"suite_id": "unknown"}, {"q": "x" * 101}])
def test_invalid_query_parameters_are_rejected(history_client, params):
    assert history_client.get("/api/runs", params=params).status_code == 422
