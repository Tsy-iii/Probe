# 订单业务的 HTTP + PostgreSQL 集成测试。
# 每条用例由 conftest.py 准备商品 1：单价 1990 分、库存 5；不会沿用上一条数据。
# 建议先读 test_create_query_and_pay，再读取消、支付幂等、并发与回滚场景。
# HTTP 响应检查服务怎么回答；数据库检查修改是否真的正确落库。

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update

from probe.config import Settings
from probe.main import create_app
from probe.models import Order, PaymentEvent, Product


pytestmark = pytest.mark.postgres
# 给本文件所有用例标记 postgres，方便单独选择数据库集成测试。


def create(client, quantity=2):
    # 常用的数据准备动作：创建 pending 订单，默认购买 2 件；返回订单 JSON。
    response = client.post("/orders", json={"product_id": 1, "quantity": quantity})
    assert response.status_code == 201, response.text
    return response.json()


def payment(order, *, event="event-001", amount=None):
    # 组装支付通知：默认金额取订单总额，调用方可覆盖金额来验证错误场景。
    return {"event_id": event, "order_id": order["id"], "amount_cents": order["total_amount_cents"] if amount is None else amount}


def snapshot(database):
    # 返回真实数据库快照，固定顺序为：(商品 1 库存, 订单行数, 支付事件行数)。
    # 后面 (3, 1, 0) 等断言必须按这个顺序解读。
    with database.sessions() as session:
        return (
            session.get(Product, 1).stock,
            session.scalar(select(func.count()).select_from(Order)),
            session.scalar(select(func.count()).select_from(PaymentEvent)),
        )


def status_in_db(database, order):
    # HTTP 的订单 ID 是字符串；转换为 UUID 后直接核查持久化状态。
    with database.sessions() as session:
        return session.get(Order, UUID(order["id"])).status


def concurrent(client, calls):
    # Barrier 让所有线程准备好后一起发请求，制造请求重叠。
    # 每次请求使用独立数据库 Session；正确性由 PostgreSQL 锁／事务保证，
    # Barrier 不保护库存，也不是业务代码里的全局锁。
    barrier = Barrier(len(calls))
    def run(call):
        barrier.wait(timeout=5)
        return call()
    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        return list(executor.map(run, calls))


def test_create_query_and_pay(client, database):
    # 先确认数据库可访问，且本用例的初始库存确实为 5。
    assert client.get("/health/ready").json()["database"] == "postgresql"
    assert client.get("/products/1").json()["stock"] == 5
    order = create(client)
    # 买 2 件：总额 1990 × 2 = 3980 分；创建就扣库存，状态等待支付。
    assert order["quantity"] == 2
    assert order["total_amount_cents"] == 3980
    assert order["status"] == "pending"
    assert order["created_at"] and order["updated_at"]
    assert client.get(f"/orders/{order['id']}").json() == order
    # 库存 5 - 2 = 3；新增 1 个订单；尚无成功支付事件。
    assert snapshot(database) == (3, 1, 0)
    response = client.post("/payments/callback", json=payment(order))
    # 支付成功只改变状态并登记事件，不会第二次扣库存。
    assert response.status_code == 200
    assert response.json()["order"]["status"] == "paid"
    assert response.json()["duplicate"] is False
    assert client.get(f"/orders/{order['id']}").json()["status"] == "paid"
    assert status_in_db(database, order) == "paid"
    # 支付后仍是库存 3、订单 1；支付事件由 0 变为 1。
    assert snapshot(database) == (3, 1, 1)


def test_cancel_and_repeat_do_not_restore_twice(client, database):
    # 创建先扣 2 件，取消应恢复 2 件；重复取消不能再次恢复。
    order = create(client)
    first = client.post(f"/orders/{order['id']}/cancel")
    second = client.post(f"/orders/{order['id']}/cancel")
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == "cancelled"
    # 完整响应相同，也意味着重复取消没有再次改更新时间。
    assert second.json() == first.json()
    assert status_in_db(database, order) == "cancelled"
    assert snapshot(database) == (5, 1, 0)


def test_missing_product_and_insufficient_stock_leave_no_order(client, database):
    # 两类业务失败分别检查：商品不存在为 404；库存 5 买 6 件为 409。
    # 每次失败后都查数据库，确保没有残留订单或错误扣减库存。
    missing = client.post("/orders", json={"product_id": 999, "quantity": 1})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "product_not_found"
    assert snapshot(database) == (5, 0, 0)
    insufficient = client.post("/orders", json={"product_id": 1, "quantity": 6})
    assert insufficient.status_code == 409
    assert insufficient.json()["error"]["code"] == "insufficient_stock"
    assert snapshot(database) == (5, 0, 0)


@pytest.mark.parametrize("operation", ["query", "cancel", "pay"])
def test_missing_order_is_404(client, database, operation):
    # UUID 格式合法但不对应任何订单，三种操作都应明确返回不存在。
    missing = str(uuid4())
    if operation == "query":
        response = client.get(f"/orders/{missing}")
    elif operation == "cancel":
        response = client.post(f"/orders/{missing}/cancel")
    else:
        response = client.post("/payments/callback", json={"event_id": "missing", "order_id": missing, "amount_cents": 1990})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "order_not_found"
    assert snapshot(database) == (5, 0, 0)


def test_amount_mismatch_rolls_back_event_and_can_be_corrected(client, database):
    # 错误金额不能支付；事件写入也必须回滚，不能提前占用 event_id。
    order = create(client)
    wrong = client.post("/payments/callback", json=payment(order, amount=1))
    assert wrong.status_code == 409
    assert wrong.json()["error"]["code"] == "payment_amount_mismatch"
    assert status_in_db(database, order) == "pending"
    assert snapshot(database) == (3, 1, 0)
    # 用同一事件 ID 改正金额，应作为首次成功处理，而非重复回调。
    corrected = client.post("/payments/callback", json=payment(order))
    assert corrected.status_code == 200
    assert corrected.json()["duplicate"] is False
    assert snapshot(database) == (3, 1, 1)


def test_duplicate_and_conflicting_payment_events(client, database):
    # 相同事件、相同内容：返回 duplicate=true，订单与更新时间不再变化。
    order = create(client, quantity=1)
    original = client.post("/payments/callback", json=payment(order))
    duplicate = client.post("/payments/callback", json=payment(order))
    assert original.status_code == duplicate.status_code == 200
    assert original.json()["duplicate"] is False
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["order"] == original.json()["order"]
    # 同一个 event_id 改金额，或改成另一个存在的订单，均属于内容冲突。
    conflict = client.post("/payments/callback", json=payment(order, amount=1))
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "event_conflict"
    other = create(client, quantity=1)
    conflict_order = client.post("/payments/callback", json=payment(other))
    assert conflict_order.status_code == 409
    assert conflict_order.json()["error"]["code"] == "event_conflict"
    assert status_in_db(database, other) == "pending"
    assert snapshot(database) == (3, 2, 1)


def test_cancelled_cannot_be_paid_and_paid_cannot_be_cancelled(client, database):
    # 检查状态边界：取消后迟到支付不生效；支付后也不能通过取消恢复库存。
    cancelled = create(client, quantity=1)
    assert client.post(f"/orders/{cancelled['id']}/cancel").status_code == 200
    late = client.post("/payments/callback", json=payment(cancelled))
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "order_cancelled"
    assert status_in_db(database, cancelled) == "cancelled"
    assert snapshot(database) == (5, 1, 0)
    paid = create(client, quantity=1)
    assert client.post("/payments/callback", json=payment(paid)).status_code == 200
    rejected = client.post(f"/orders/{paid['id']}/cancel")
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "order_already_paid"
    assert status_in_db(database, paid) == "paid"
    assert snapshot(database) == (4, 2, 1)


def test_concurrent_creation_cannot_oversell(client, database):
    # 为本场景把库存改成 3，让 8 个请求各买 1 件，制造真实数据库竞争。
    with database.sessions.begin() as session:
        session.execute(update(Product).where(Product.id == 1).values(stock=3))
    responses = concurrent(client, [lambda: client.post("/orders", json={"product_id": 1, "quantity": 1}) for _ in range(8)])
    # Counter 统计状态码分布：恰好 3 个创建成功，其他 5 个库存不足。
    assert Counter(r.status_code for r in responses) == {201: 3, 409: 5}
    assert all(r.json()["error"]["code"] == "insufficient_stock" for r in responses if r.status_code == 409)
    assert snapshot(database) == (0, 3, 0)
    with database.sessions() as session:
        # 不只看响应：实际订单购买数量之和也只能是 3，库存应恰好为 0。
        assert session.scalar(select(func.sum(Order.quantity))) == 3
        assert set(session.scalars(select(Order.status))) == {"pending"}


def test_concurrent_duplicate_callback_and_cancel(client, database):
    # 同一支付事件并发到达 4 次，只能登记一次，另外 3 次识别为重复。
    paid = create(client, quantity=1)
    responses = concurrent(client, [lambda: client.post("/payments/callback", json=payment(paid)) for _ in range(4)])
    assert all(r.status_code == 200 for r in responses)
    assert Counter(r.json()["duplicate"] for r in responses) == {False: 1, True: 3}
    # 再创建一个订单并发取消 4 次，库存同样只能恢复一次。
    pending = create(client, quantity=1)
    cancels = concurrent(client, [lambda: client.post(f"/orders/{pending['id']}/cancel") for _ in range(4)])
    assert all(r.status_code == 200 and r.json()["status"] == "cancelled" for r in cancels)
    assert snapshot(database) == (4, 2, 1)


def test_competing_payment_events_and_payment_cancel_race(client, database):
    # 两个不同合法事件可以登记，但 paid 状态只能完成一次业务转换。
    paid = create(client, quantity=1)
    callbacks = concurrent(client, [lambda e=e: client.post("/payments/callback", json=payment(paid, event=e)) for e in ["a", "b"]])
    assert all(r.status_code == 200 and r.json()["order"]["status"] == "paid" for r in callbacks)
    assert status_in_db(database, paid) == "paid"
    pending = create(client, quantity=1)
    # 对另一个 pending 订单同时支付和取消；不预设哪一方抢到订单锁。
    pay, cancel = concurrent(client, [
        lambda: client.post("/payments/callback", json=payment(pending, event="race")),
        lambda: client.post(f"/orders/{pending['id']}/cancel"),
    ])
    final = status_in_db(database, pending)
    # 允许任一方先完成，但状态、HTTP 响应、库存与事件数量必须成套一致。
    if final == "paid":
        # 两个订单各扣 1 件：库存 3；第一个订单有 2 个事件，第二个新增 1 个。
        assert (pay.status_code, cancel.status_code) == (200, 409)
        assert snapshot(database) == (3, 2, 3)
    else:
        # 第二个订单取消并退回库存：库存 4；只保留第一个订单的 2 个事件。
        assert final == "cancelled"
        assert (pay.status_code, cancel.status_code) == (409, 200)
        assert snapshot(database) == (4, 2, 2)


def test_insert_failure_really_rolls_back_stock(client, database):
    # 在数据库订单 INSERT 阶段确定失败，验证先前已执行的库存 UPDATE 会回滚。
    # 触发器与函数只建在本用例的独立 schema 中，清理时不会影响演示库。
    with database.engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION reject_order() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'intentional test insert failure'; END; $$
        """))
        connection.execute(text("CREATE TRIGGER reject_order BEFORE INSERT ON orders FOR EACH ROW EXECUTE FUNCTION reject_order()"))
    response = client.post("/orders", json={"product_id": 1, "quantity": 2}, headers={"X-Request-ID": "rollback-001"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_error"
    assert response.json()["request_id"] == "rollback-001"
    # 如果事务正确，仍是初始库存 5、零订单、零事件；HTTP 503 本身不足以证明。
    assert snapshot(database) == (5, 0, 0)


def test_fault_injection_leaves_database_unchanged(database):
    # 这里的 error 在业务和数据库操作前返回，与上面的 INSERT 回滚测试不同。
    app = create_app(Settings(app_env="test", fault_injection_enabled=True), database)
    with TestClient(app) as client:
        for _ in range(2):
            response = client.post("/orders", json={"product_id": 1, "quantity": 2}, headers={"X-Probe-Fault": "error"})
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "injected_error"
            assert snapshot(database) == (5, 0, 0)
