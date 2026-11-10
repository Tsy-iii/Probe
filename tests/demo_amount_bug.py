# 明确的隔离故障对照：此文件不匹配 test_*.py，因此默认 pytest 不收集它。
# 平台显式选择时执行两条用例：故障实现失败，正确实现通过。
# monkeypatch 只影响本次测试进程；不修改 service.py 或正在运行的 8002 服务。
import inspect

from probe import main as order_api
from probe.models import Product
from probe.service import create_order as correct_create_order
from probe.test_evidence import check, database_snapshot, record_http_evidence


def verify_amount_contract(client, database, request, *, faulty):
    before = database_snapshot(database)
    response = client.post("/orders", json={"product_id": 1, "quantity": 2}, headers={"X-Request-ID": "amount-bug-001" if faulty else "amount-correct-001"})
    order = response.json()
    after = database_snapshot(database)
    # 根据真正实现的位置提供提示；这是示例作者说明，不是平台自动判断根因。
    lines, start = inspect.getsourcelines(correct_create_order)
    formula_line = next((start + index for index, line in enumerate(lines) if "total = product.price_cents * payload.quantity" in line), start)
    record_http_evidence(
        request, response, label="故障实现：金额漏乘数量" if faulty else "正确实现：同一金额契约回归",
        before=before, after=after,
        checks=[
            check("HTTP 状态码", 201, response.status_code),
            check("订单数量", 2, order.get("quantity")),
            check("订单总金额（分）", 3980, order.get("total_amount_cents")),
            check("剩余库存", 3, after["products"][0]["stock"]),
            check("订单行数", 1, after["order_count"]),
        ],
        hint="本隔离示例临时把总金额改为单价，漏掉 quantity。正确计算应为 price_cents × quantity。" if faulty else "未注入故障，使用原订单服务公式；同样购买 2 件，总额应为 3980 分。",
        related_code={"file": "probe/service.py", "line": formula_line},
    )
    assert response.status_code == 201
    assert order["quantity"] == 2
    # 故障场景这里应真实失败：HTTP 201 仍然可能携带错误业务金额。
    assert order["total_amount_cents"] == 3980, f"购买 2 件应为 3980 分，实际为 {order['total_amount_cents']} 分"
    assert after["orders"][0]["total_amount_cents"] == 3980
    assert after["products"][0]["stock"] == 3


def test_amount_with_faulty_implementation(client, database, monkeypatch, request):
    def faulty_create_order(session, payload):
        order = correct_create_order(session, payload)
        # 在真实事务里制造错误金额；数量和库存扣减仍使用正常逻辑。
        order.total_amount_cents = session.get(Product, payload.product_id).price_cents
        session.flush()
        return order

    monkeypatch.setattr(order_api, "create_order", faulty_create_order)
    verify_amount_contract(client, database, request, faulty=True)


def test_amount_with_correct_implementation(client, database, request):
    # 前一条的 monkeypatch 已由 pytest 恢复，数据库也由 fixture 重建。
    verify_amount_contract(client, database, request, faulty=False)
