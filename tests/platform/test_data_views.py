"""数据浏览的接口集成测试：使用真实 PostgreSQL，绝不读取／清理手动演示库。

阅读顺序：通过订单接口准备数据 → 调用只读浏览接口 → 核对关联和数据库。
不启动 Worker，避免它领取本用例用于展示的任务记录。
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from probe.db import Database
from probe.models import Order, Product
from probe.platform.data_views import view_session
from probe.platform.main import create_app
from probe.platform.models import RunAttempt, TestRun as RunRecord, WorkerRecord


pytestmark = pytest.mark.postgres


@pytest.fixture
def data_client(platform_settings, platform_database, database):
    # 两个引擎均指向随机测试 schema；生产默认配置则连接 probe / probe_platform。
    with TestClient(create_app(platform_settings, platform_database, order_database=database)) as client:
        yield client


def test_business_snapshot_matches_http_changes(data_client, client, database):
    paid = client.post("/orders", json={"product_id": 1, "quantity": 2})
    assert paid.status_code == 201
    order_id = paid.json()["id"]
    assert client.post("/payments/callback", json={"event_id": "view-paid", "order_id": order_id, "amount_cents": 3980}).status_code == 200
    cancelled = client.post("/orders", json={"product_id": 1, "quantity": 1})
    assert cancelled.status_code == 201
    assert client.post(f"/orders/{cancelled.json()['id']}/cancel").status_code == 200

    response = data_client.get("/api/data/orders")
    assert response.status_code == 200
    data = response.json()
    assert data["source"]["database"] == "probe_test"
    assert data["source"]["schema"] == database.schema
    assert data["source"]["readonly"] is True
    assert data["counts"] == {"products": 1, "orders": 2, "payment_events": 1}
    assert data["status_counts"] == {"pending": 0, "paid": 1, "cancelled": 1}
    assert data["products"][0]["stock"] == 3
    assert data["products"][0]["price_cents"] == "1990"
    order = next(row for row in data["orders"] if row["id"] == order_id)
    assert (order["product_id"], order["product_name"], order["total_amount_cents"], order["payment_event_count"]) == (1, "Test Notebook", "3980", 1)
    event = data["payment_events"][0]
    assert (event["event_id"], event["order_id"], event["amount_cents"], event["order_status"]) == ("view-paid", order_id, "3980", "paid")
    # 两次浏览结果的业务内容相同（读取时间除外）；核对实际库存与订单状态。
    again = data_client.get("/api/data/orders").json()
    assert {k: v for k, v in again.items() if k != "source"} == {k: v for k, v in data.items() if k != "source"}
    with database.sessions() as session:
        assert session.get(Product, 1).stock == 3
        assert session.get(Order, UUID(order_id)).status == "paid"


def test_amount_precision_and_full_counts_survive_row_limit(data_client, database):
    # 大于 JS Number 的精确整数上限；必须逐位保留，不能把 bigint 转成浮点数。
    amount = 9007199254740993
    with database.sessions.begin() as session:
        session.get(Product, 1).price_cents = amount
        session.add(Product(id=2, name="Second", price_cents=1, stock=0))
        for _ in range(2):
            session.add(Order(product_id=1, quantity=1, total_amount_cents=amount))
    response = data_client.get("/api/data/orders?limit=1")
    assert response.status_code == 200
    data = response.json()
    assert data["counts"] == {"products": 2, "orders": 2, "payment_events": 0}
    assert len(data["products"]) == len(data["orders"]) == data["source"]["row_limit"] == 1
    assert data["products"][0]["price_cents"] == data["orders"][0]["total_amount_cents"] == str(amount)


def test_snapshot_is_repeatable_and_postgres_rejects_writes(database):
    # 独立连接提交新库存后，已有快照仍读取旧值：多表不会混入不同时间的数据。
    with view_session(database) as reader:
        assert reader.scalar(select(Product.stock)) == 5
        with database.sessions.begin() as writer:
            writer.get(Product, 1).stock = 4
        assert reader.scalar(select(Product.stock)) == 5
    with pytest.raises(DBAPIError) as rejected:
        with view_session(database) as reader:
            reader.execute(text("UPDATE products SET stock=0"))
    assert rejected.value.orig.sqlstate == "25006"  # PostgreSQL read_only_sql_transaction
    with database.sessions() as session:
        assert session.get(Product, 1).stock == 4


def test_platform_relationships_limits_and_expired_heartbeat(data_client, platform_database):
    now = datetime.now(timezone.utc)
    online_id, stale_id = uuid4(), uuid4()
    run_id = uuid4()
    with platform_database.sessions.begin() as session:
        session.add_all([
            WorkerRecord(id=online_id, process_id=100, heartbeat_at=now),
            WorkerRecord(id=stale_id, process_id=101, heartbeat_at=now - timedelta(days=1)),
            RunRecord(id=run_id, suite_id="contract", status="running", idempotency_key="view-task", request_hash="secret-hash", attempt_count=1, report={"hidden": "large-report"}),
        ])
        session.flush()
        session.add(RunAttempt(run_id=run_id, number=1, worker_id=online_id, isolation_schema="run_" + uuid4().hex, report={"hidden": "attempt-report"}))

    response = data_client.get("/api/data/platform")
    assert response.status_code == 200
    data = response.json()
    assert data["source"]["database"] == "probe_platform_test"
    assert data["source"]["schema"] == platform_database.schema
    assert data["source"]["readonly"] is True
    assert data["counts"] == {"runs": 1, "attempts": 1, "workers": 2}
    assert data["online_workers"] == 1
    assert data["runs"][0]["id"] == data["attempts"][0]["run_id"] == str(run_id)
    assert data["attempts"][0]["worker_id"] == str(online_id)
    stale = next(row for row in data["workers"] if row["id"] == str(stale_id))
    assert stale["last_status"] == "online" and stale["online"] is False
    for hidden in ["secret-hash", "large-report", "attempt-report", "database_url", "password"]:
        assert hidden not in response.text
    limited = data_client.get("/api/data/platform?limit=1").json()
    assert limited["counts"]["workers"] == 2 and len(limited["workers"]) == 1
    with platform_database.sessions() as session:
        assert session.get(RunRecord, run_id).report == {"hidden": "large-report"}


@pytest.mark.parametrize("path", ["orders", "platform"])
@pytest.mark.parametrize("limit", [0, -1, 101])
def test_limit_validation_changes_one_field(data_client, path, limit):
    assert data_client.get(f"/api/data/{path}?limit={limit}").status_code == 422


def test_missing_business_tables_do_not_break_platform_view(platform_settings, platform_database, database):
    # 已存在的独立测试库中指定不存在的 schema，稳定模拟未初始化业务库。
    unavailable = Database(database.engine.url.render_as_string(hide_password=False), schema="missing_" + uuid4().hex)
    try:
        with TestClient(create_app(platform_settings, platform_database, order_database=unavailable)) as client:
            response = client.get("/api/data/orders")
            assert response.status_code == 503
            assert "Order demo database is unavailable" in response.json()["detail"]
            platform = client.get("/api/data/platform")
            assert platform.status_code == 200
            assert platform.json()["counts"] == {"runs": 0, "attempts": 0, "workers": 0}
    finally:
        unavailable.close()
