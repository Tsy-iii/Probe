# 库存刚好够的边界测试：HTTP 响应与实际数据库结果一起检查。
# client 依赖 conftest.py 的 database fixture，初始库存为 5、单价为 1990 分。
import pytest

from probe.test_evidence import check, database_snapshot, record_http_evidence


pytestmark = pytest.mark.postgres


def test_stock_exactly_enough(client, database, request):
    before = database_snapshot(database)
    # 操作：一次购买全部 5 件库存。这里发送的是应用内 HTTP 测试请求。
    response = client.post(
        "/orders",
        json={"product_id": 1, "quantity": 5},
        headers={"X-Request-ID": "stock-boundary-create-001"},
    )
    order = response.json()
    after_create = database_snapshot(database)
    record_http_evidence(
        request, response, label="库存刚好够：购买 5 件",
        before=before, after=after_create,
        checks=[
            check("HTTP 状态码", 201, response.status_code),
            check("订单数量", 5, order.get("quantity")),
            check("订单总金额（分）", 9950, order.get("total_amount_cents")),
            check("剩余库存", 0, after_create["products"][0]["stock"]),
            check("订单行数", 1, after_create["order_count"]),
        ],
    )
    # HTTP 201 表示创建成功；响应 JSON 中还应包含正确的业务数据。
    assert response.status_code == 201

    # 将 JSON 响应解析为 Python 字典，才能通过字段名读取数量和金额。
    order = response.json()
    # 断言 1.订单数量是5
    assert order["quantity"] == 5
    # 断言 2：1990 分 × 5 件 = 9950 分，金额使用整数分。
    assert order["total_amount_cents"] == 9950
    assert order["status"] == "pending"

    # 直接查询真实 PostgreSQL：库存已扣完，而且确实写入了这一笔订单。
    assert after_create["products"][0]["stock"] == 0
    assert after_create["order_count"] == 1
    assert after_create["orders"][0]["id"] == order["id"]
    assert after_create["orders"][0]["quantity"] == 5
    assert after_create["orders"][0]["total_amount_cents"] == 9950

    # 库存已为 0，再买 1 件是业务上的库存不足，应该返回 409。
    rejected = client.post("/orders", json={"product_id": 1, "quantity": 1}, headers={"X-Request-ID": "stock-boundary-rejected-001"})
    after_rejection = database_snapshot(database)
    record_http_evidence(
        request, rejected, label="库存已耗尽：再次购买被拒绝",
        before=after_create, after=after_rejection,
        checks=[
            check("HTTP 状态码", 409, rejected.status_code),
            check("业务错误码", "insufficient_stock", rejected.json().get("error", {}).get("code")),
            check("剩余库存", 0, after_rejection["products"][0]["stock"]),
            check("订单行数", 1, after_rejection["order_count"]),
            check("数据库内容保持不变", True, after_rejection == after_create),
        ],
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "insufficient_stock"
    # 失败不能写入第二个订单，也不能撤销第一次成功创建的订单。
    assert after_rejection == after_create
