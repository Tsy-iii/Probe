"""通过真实 loopback HTTP 地址测试订单；每条用例独立数据、独立服务进程。"""
import socket
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from probe.test_evidence import check, database_snapshot, record_http_evidence, record_transport_evidence

pytestmark = pytest.mark.postgres


def create(http_checks, quantity=1):
    response = http_checks.send('POST', '/orders', json={'product_id': 1, 'quantity': quantity}, label='HTTP 创建订单', expected_status=201, expected_fields={'quantity': quantity, 'total_amount_cents': 1990 * quantity, 'status': 'pending'}, headers={'X-Request-ID': 'http-create-001'})
    assert response.status_code == 201
    return response.json()


def payment(order, amount=None):
    return {'event_id': 'http-pay-001', 'order_id': order['id'], 'amount_cents': amount if amount is not None else order['total_amount_cents']}


def test_http_create_query_and_pay(http_checks, database):
    # 先购买 3 件，再查询和支付；金额与库存都直接核对 PostgreSQL。
    order = create(http_checks, 3)
    assert order['quantity'] == 3 and order['total_amount_cents'] == 5970 and order['status'] == 'pending'
    queried = http_checks.send('GET', f"/orders/{order['id']}", label='HTTP 查询订单', expected_status=200)
    assert queried.status_code == 200 and queried.json()['id'] == order['id']
    paid = http_checks.send('POST', '/payments/callback', json=payment(order), label='HTTP 支付回调', expected_status=200, expected_fields={'order.status': 'paid'})
    assert paid.status_code == 200 and paid.json()['order']['status'] == 'paid'
    snapshot = database_snapshot(database)
    assert snapshot['products'][0]['stock'] == 2
    assert snapshot['orders'][0]['total_amount_cents'] == 5970
    assert snapshot['payment_event_count'] == 1


def test_http_cancel_twice_restores_once(http_checks, database):
    order = create(http_checks, 2)
    first = http_checks.send('POST', f"/orders/{order['id']}/cancel", label='HTTP 首次取消', expected_status=200)
    assert first.status_code == 200 and first.json()['status'] == 'cancelled'
    after = database_snapshot(database)
    repeat = http_checks.send('POST', f"/orders/{order['id']}/cancel", label='HTTP 重复取消', expected_status=200)
    assert repeat.status_code == 200 and repeat.json()['status'] == 'cancelled'
    assert after['products'][0]['stock'] == 5
    assert database_snapshot(database) == after


def test_http_payment_deduplication_and_conflict(http_checks, database):
    order = create(http_checks)
    assert http_checks.send('POST', '/payments/callback', json=payment(order), label='HTTP 首次支付', expected_status=200).status_code == 200
    before = database_snapshot(database)
    repeated = http_checks.send('POST', '/payments/callback', json=payment(order), label='HTTP 重复支付', expected_status=200)
    assert repeated.status_code == 200 and repeated.json()['duplicate'] is True
    conflict = http_checks.send('POST', '/payments/callback', json=payment(order, 1), label='HTTP 事件内容冲突', expected_status=409)
    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'event_conflict'
    assert database_snapshot(database) == before and before['payment_event_count'] == 1


def test_http_amount_mismatch_leaves_no_event(http_checks, database):
    order = create(http_checks, 2)
    before = database_snapshot(database)
    wrong = http_checks.send('POST', '/payments/callback', json=payment(order, 1), label='HTTP 金额不匹配', expected_status=409)
    assert wrong.status_code == 409 and wrong.json()['error']['code'] == 'payment_amount_mismatch'
    assert database_snapshot(database) == before and before['payment_event_count'] == 0
    correct = http_checks.send('POST', '/payments/callback', json=payment(order), label='HTTP 正确金额支付', expected_status=200)
    assert correct.status_code == 200
    assert database_snapshot(database)['orders'][0]['status'] == 'paid'


@pytest.mark.parametrize('quantity', [0, -1])
def test_http_invalid_quantity_changes_one_field(http_checks, database, quantity):
    before = database_snapshot(database)
    response = http_checks.send('POST', '/orders', json={'product_id': 1, 'quantity': quantity}, label='HTTP 数量校验', expected_status=422)
    assert response.status_code == 422
    assert response.json()['details'][0]['loc'] == ['body', 'quantity']
    assert database_snapshot(database) == before


@pytest.mark.parametrize('product_id,quantity,status,code', [(1, 6, 409, 'insufficient_stock'), (999999, 1, 404, 'product_not_found')])
def test_http_business_rejection_preserves_database(http_checks, database, product_id, quantity, status, code):
    before = database_snapshot(database)
    response = http_checks.send('POST', '/orders', json={'product_id': product_id, 'quantity': quantity}, label='HTTP 拒绝无效业务请求', expected_status=status, expected_fields={'error.code': code})
    assert response.status_code == status and response.json()['error']['code'] == code
    assert database_snapshot(database) == before


def test_http_order_state_conflicts(http_checks, database):
    paid = create(http_checks)
    assert http_checks.send('POST', '/payments/callback', json=payment(paid), label='HTTP 支付订单', expected_status=200).status_code == 200
    before = database_snapshot(database)
    cancelled = http_checks.send('POST', f"/orders/{paid['id']}/cancel", label='HTTP 已支付订单取消', expected_status=409)
    assert cancelled.status_code == 409 and cancelled.json()['error']['code'] == 'order_already_paid'
    assert database_snapshot(database) == before
    pending = create(http_checks)
    assert http_checks.send('POST', f"/orders/{pending['id']}/cancel", label='HTTP 取消待支付订单', expected_status=200).status_code == 200
    before = database_snapshot(database)
    late = http_checks.send('POST', '/payments/callback', json={**payment(pending), 'event_id': 'late-http-001'}, label='HTTP 取消后迟到支付', expected_status=409)
    assert late.status_code == 409 and late.json()['error']['code'] == 'order_cancelled'
    assert database_snapshot(database) == before


def test_http_concurrent_creation_cannot_oversell(http_service, database, request):
    before = database_snapshot(database)
    with http_service.client() as client, ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda number: client.post('/orders', json={'product_id': 1, 'quantity': 1}, headers={'X-Request-ID': f'http-stock-{number}'}), range(8)))
    after = database_snapshot(database)
    passed = sum(response.status_code == 201 for response in responses)
    rejected = sum(response.status_code == 409 for response in responses)
    for response in responses:
        record_http_evidence(request, response, label='HTTP 并发批次证据', before=before, after=after,
                             checks=[check('批次成功订单数', 5, passed), check('批次拒绝数', 3, rejected), check('批次库存', 0, after['products'][0]['stock'])],
                             hint='这里的数据库快照对应整个并发批次，不代表某一条请求独自造成的变化。', transport={'mode': 'network', 'service_id': http_service.id, 'base_url': http_service.base_url})
    assert passed == 5 and rejected == 3
    assert after['products'][0]['stock'] == 0 and after['order_count'] == 5


def test_http_fixed_error_is_reproducible(http_service_factory, database, request):
    before = database_snapshot(database)
    with http_service_factory(allow_faults=True) as service, service.client() as client:
        response = client.post('/orders', json={'product_id': 1, 'quantity': 1}, headers={'X-Probe-Fault': 'error', 'X-Request-ID': 'http-fixed-error-001'})
        after = database_snapshot(database)
        record_http_evidence(request, response, label='HTTP 显式固定错误', before=before, after=after, checks=[check('HTTP 状态码', 503, response.status_code)], transport={'mode': 'network', 'base_url': service.base_url})
        assert response.status_code == 503 and response.json()['error']['code'] == 'injected_error'
        assert after == before


def test_http_read_timeout_has_no_response(http_service_factory, database, request):
    # GET 查询用于演示超时，避免将“客户端超时”错误推断成 POST 已回滚。
    before = database_snapshot(database)
    with http_service_factory(allow_faults=True) as service, service.client(timeout=0.05) as client:
        started = time.perf_counter()
        with pytest.raises(httpx.ReadTimeout) as failure:
            client.get('/products/1', headers={'X-Probe-Fault': 'delay', 'X-Request-ID': 'http-read-timeout-001'})
        after = database_snapshot(database)
        record_transport_evidence(request, failure.value, label='HTTP 读取超时：没有响应', before=before, after=after,
                                  timeout_seconds=0.05, elapsed_seconds=time.perf_counter() - started, checks=[check('异常类型', 'ReadTimeout', type(failure.value).__name__)],
                                  hint='请求超时不代表服务未执行。本例是 GET 查询，不写业务数据；POST 超时必须查询实际结果再判断。')
        assert after == before


def test_http_connection_failure_has_no_response(database, request):
    # 保留一个绑定但未监听的端口；连接失败可由 OS 表现为拒绝或连接超时。
    before = database_snapshot(database)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{reservation.getsockname()[1]}/products/1'
        started = time.perf_counter()
        with httpx.Client(timeout=0.5, trust_env=False, transport=httpx.HTTPTransport(retries=0)) as client:
            with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)) as failure:
                client.get(url, headers={'X-Request-ID': 'http-connect-failure-001'})
        after = database_snapshot(database)
        record_transport_evidence(request, failure.value, label='HTTP 连接失败：端口未监听', before=before, after=after,
                                  timeout_seconds=0.5, elapsed_seconds=time.perf_counter() - started, checks=[check('连接阶段失败', True, isinstance(failure.value, (httpx.ConnectError, httpx.ConnectTimeout)))],
                                  hint='此地址只有端口保留，没有 HTTP 服务，因此没有状态码，也未发送到订单服务。')
        assert after == before
