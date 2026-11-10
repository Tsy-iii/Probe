"""演示操作 API：独立 PostgreSQL schema；故障响应不重试，不写用户演示库。

多数用例通过 ASGI 转发到真实订单应用，验证 HTTP 与数据库结果。
单独的 TCP 用例启动本测试拥有的订单进程，验证实际网络调用与清理。
"""
import json
from contextlib import contextmanager
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from probe.config import Settings
from probe.http_testing import HTTPTestService
from probe.main import create_app as create_order_app
from probe.models import Order, PaymentEvent, Product
from probe.platform.main import create_app
from probe.test_evidence import check, database_snapshot, record_http_evidence

pytestmark = pytest.mark.postgres


class DemoChecks:
    """先保存请求和实际数据库前后结果，再由用例断言；失败也不会丢证据。"""
    def __init__(self, client, database, request):
        self.client, self.database, self.request = client, database, request

    def send(self, method, path, *, expected_status=None, expected_fields=None, expected_database=None, **kwargs):
        before = database_snapshot(self.database)
        response = self.client.request(method, path, **kwargs)
        after = database_snapshot(self.database)
        checks = [check('HTTP 状态码', expected_status, response.status_code)] if expected_status is not None else []
        for name, expected in (expected_fields or {}).items():
            actual = response.json()
            for field in name.split('.'):
                actual = actual.get(field) if isinstance(actual, dict) else None
            checks.append(check(name, expected, actual))
        values = {'stock': after['products'][0]['stock'], 'order_count': after['order_count'], 'payment_event_count': after['payment_event_count'],
                  'order_status': after['orders'][0]['status'] if after['orders'] else None}
        names = {'stock': '商品 1 库存', 'order_count': '订单数', 'payment_event_count': '支付事件数', 'order_status': '数据库订单状态'}
        for name, expected in (expected_database or {}).items():
            checks.append(check(names[name], expected, values[name]))
        record_http_evidence(self.request, response, label=f'演示入口 {method} {path}', before=before, after=after, checks=checks,
                             hint='此处检查平台演示入口；响应包含上游订单 API 的真实状态和原始 JSON。ASGI 与 TCP 范围按用例说明区分。')
        return response

    def get(self, path, **kwargs):
        return self.send('GET', path, **kwargs)

    def post(self, path, **kwargs):
        return self.send('POST', path, **kwargs)


@contextmanager
def recorded_client(app, database, request):
    with TestClient(app) as client:
        yield DemoChecks(client, database, request)


@pytest.fixture
def demo_client(platform_settings, platform_database, database, request):
    app = create_order_app(Settings(app_env='test', fault_injection_enabled=False), database)
    settings = platform_settings.model_copy(update={'app_env': 'test'})
    with recorded_client(create_app(settings, platform_database, order_database=database, demo_transport=httpx.ASGITransport(app=app)), database, request) as client:
        yield client


def state(database):
    with database.sessions() as session:
        return (session.get(Product, 1).stock, session.scalar(select(func.count()).select_from(Order)), session.scalar(select(func.count()).select_from(PaymentEvent)))


def create(client, quantity=2, *, expected_status=201):
    before = database_snapshot(client.database)
    expected = {'stock': before['products'][0]['stock'], 'order_count': before['order_count'], 'payment_event_count': before['payment_event_count']}
    fields = None
    if expected_status == 201:
        fields = {'data.quantity': quantity, 'data.total_amount_cents': str(before['products'][0]['price_cents'] * quantity), 'data.status': 'pending'}
        expected.update(stock=expected['stock'] - quantity, order_count=expected['order_count'] + 1, order_status='pending')
    return client.post('/api/demo/orders', json={'product_id': 1, 'quantity': quantity}, headers={'X-Request-ID': 'demo-create-001'},
                       expected_status=expected_status, expected_fields=fields, expected_database=expected)


def pay(client, order, event='demo-pay-001', amount=None, *, expected_status=200, duplicate=False):
    before = database_snapshot(client.database)
    expected = {'stock': before['products'][0]['stock'], 'order_count': before['order_count'], 'payment_event_count': before['payment_event_count']}
    fields = None
    if expected_status == 200:
        fields = {'data.order.status': 'paid', 'data.duplicate': duplicate}
        expected.update(payment_event_count=expected['payment_event_count'] + (0 if duplicate else 1), order_status='paid')
    return client.post('/api/demo/payments/callback', json={
        'order_id': order['id'], 'event_id': event,
        'amount_cents': amount if amount is not None else order['total_amount_cents'],
    }, expected_status=expected_status, expected_fields=fields, expected_database=expected)


def test_create_query_pay_and_repeat_keep_real_status_and_request_id(demo_client, database):
    before = demo_client.get('/api/demo/products/1')
    assert before.status_code == 200 and before.json()['data']['stock'] == 5
    response = create(demo_client)
    record = response.json(); order = record['data']
    assert response.status_code == record['response']['status_code'] == 201
    assert record['request_id'] == record['response']['request_id'] == response.headers['X-Request-ID'] == 'demo-create-001'
    assert record['request']['path'] == '/orders'
    assert json.loads(record['request']['body_text']) == {'product_id': 1, 'quantity': 2}
    assert json.loads(record['response']['body_text'])['total_amount_cents'] == 3980
    assert order['total_amount_cents'] == '3980' and order['status'] == 'pending'
    assert record['elapsed_ms'] >= 0 and state(database) == (3, 1, 0)
    assert demo_client.get(f"/api/demo/orders/{order['id']}").json()['data']['status'] == 'pending'
    paid = pay(demo_client, order)
    assert paid.status_code == 200 and paid.json()['data']['order']['status'] == 'paid'
    assert paid.json()['data']['duplicate'] is False
    duplicate = pay(demo_client, order, duplicate=True)
    assert duplicate.status_code == 200 and duplicate.json()['data']['duplicate'] is True
    assert state(database) == (3, 1, 1)
    with database.sessions() as session:
        assert session.get(Order, UUID(order['id'])).status == 'paid'


def test_cancel_and_repeat_restore_stock_once(demo_client, database):
    order = create(demo_client).json()['data']
    path = f"/api/demo/orders/{order['id']}/cancel"
    for _ in range(2):
        response = demo_client.post(path, expected_status=200, expected_fields={'data.status':'cancelled'},
                                    expected_database={'stock':5, 'order_count':1, 'payment_event_count':0, 'order_status':'cancelled'})
        assert response.status_code == 200 and response.json()['data']['status'] == 'cancelled'
        assert state(database) == (5, 1, 0)
    late = pay(demo_client, order, expected_status=409)
    assert late.status_code == 409 and late.json()['data']['error']['code'] == 'order_cancelled'
    assert state(database) == (5, 1, 0)


def test_payment_errors_and_paid_cancel_preserve_database(demo_client, database):
    order = create(demo_client).json()['data']
    wrong = pay(demo_client, order, amount='3981', expected_status=409)
    assert wrong.status_code == wrong.json()['response']['status_code'] == 409
    assert wrong.json()['data']['error']['code'] == 'payment_amount_mismatch'
    assert state(database) == (3, 1, 0)
    assert pay(demo_client, order).status_code == 200
    conflict = pay(demo_client, order, amount='3981', expected_status=409)
    assert conflict.status_code == 409 and conflict.json()['data']['error']['code'] == 'event_conflict'
    cancelled = demo_client.post(f"/api/demo/orders/{order['id']}/cancel", expected_status=409,
                                 expected_database={'stock':3,'order_count':1,'payment_event_count':1,'order_status':'paid'})
    assert cancelled.status_code == 409 and cancelled.json()['data']['error']['code'] == 'order_already_paid'
    assert state(database) == (3, 1, 1)


@pytest.mark.parametrize('quantity', [0, -1, 1.5, True])
def test_bad_quantity_changes_only_one_field_and_writes_nothing(demo_client, database, quantity):
    response = create(demo_client, quantity, expected_status=422)
    assert response.status_code == 422
    assert response.json()['detail'][0]['loc'] == ['body', 'quantity']
    assert state(database) == (5, 0, 0)


def test_missing_and_insufficient_stock_return_business_errors(demo_client, database):
    for product, quantity, status, code in [(999999, 1, 404, 'product_not_found'), (1, 6, 409, 'insufficient_stock')]:
        response = demo_client.post('/api/demo/orders', json={'product_id': product, 'quantity': quantity}, expected_status=status,
                                    expected_fields={'data.error.code':code}, expected_database={'stock':5,'order_count':0,'payment_event_count':0})
        assert response.status_code == status and response.json()['data']['error']['code'] == code
        assert state(database) == (5, 0, 0)
    missing = demo_client.get(f'/api/demo/orders/{uuid4()}')
    assert missing.status_code == 404 and missing.json()['data']['error']['code'] == 'order_not_found'


def test_deleted_product_rejects_creation(demo_client, database):
    with database.sessions.begin() as session:
        session.execute(update(Product).where(Product.id == 1).values(is_active=False))
    response = create(demo_client, expected_status=409)
    assert response.status_code == 409 and response.json()['data']['error']['code'] == 'product_inactive'
    assert state(database) == (5, 0, 0)


def test_bigint_amount_is_exact_in_summary_raw_response_and_payment(demo_client, database):
    cents = 9_007_199_254_740_993
    with database.sessions.begin() as session:
        session.execute(update(Product).where(Product.id == 1).values(price_cents=cents))
    record = create(demo_client, 1).json(); order = record['data']
    assert order['total_amount_cents'] == str(cents)
    assert json.loads(record['response']['body_text'])['total_amount_cents'] == cents
    paid = pay(demo_client, order).json()
    assert json.loads(paid['request']['body_text'])['amount_cents'] == cents
    with database.sessions() as session:
        assert session.get(PaymentEvent, 'demo-pay-001').amount_cents == cents


@pytest.mark.parametrize('value', [0, '0', '19.90', '9223372036854775808'])
def test_callback_amount_validation_uses_integer_cents_strings(demo_client, database, value):
    order = create(demo_client).json()['data']
    response = pay(demo_client, order, amount=value, expected_status=422)
    assert response.status_code == 422
    assert response.json()['detail'][0]['loc'] == ['body', 'amount_cents']
    assert state(database) == (3, 1, 0)


@pytest.mark.parametrize('change', [{'app_env': 'production'}, {'demo_edit_enabled': False}])
def test_disabled_demo_never_calls_order_service(platform_settings, platform_database, database, change, request):
    calls = []
    settings = platform_settings.model_copy(update={'app_env': 'test', **change})
    transport = httpx.MockTransport(lambda request: calls.append(request.url) or httpx.Response(500))
    with recorded_client(create_app(settings, platform_database, order_database=database, demo_transport=transport), database, request) as client:
        response = create(client, expected_status=403)
    assert response.status_code == 403 and response.json()['transport_error']['code'] == 'demo_disabled'
    assert calls == [] and state(database) == (5, 0, 0)


def test_target_mismatch_blocks_writes_before_forwarding(platform_settings, platform_database, database, request):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={'status': 'ready', 'database_name': 'probe', 'schema': 'public', 'app_env': 'development'})
    settings = platform_settings.model_copy(update={'app_env': 'test'})
    with recorded_client(create_app(settings, platform_database, order_database=database, demo_transport=httpx.MockTransport(handler)), database, request) as client:
        response = create(client, expected_status=503)
    assert response.status_code == 503 and response.json()['transport_error']['code'] == 'order_api_target_mismatch'
    assert calls == ['/health/ready'] and state(database) == (5, 0, 0)


def test_lost_reply_after_commit_is_unknown_and_not_retried(platform_settings, platform_database, database, request):
    calls = []
    class LostReply(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx.ASGITransport(app=create_order_app(Settings(app_env='test'), database))
        async def handle_async_request(self, request):
            response = await self.inner.handle_async_request(request)
            if request.method == 'POST':
                calls.append(request.url.path)
                raise httpx.ReadTimeout('Controlled lost response after commit', request=request)
            return response
    settings = platform_settings.model_copy(update={'app_env': 'test'})
    with recorded_client(create_app(settings, platform_database, order_database=database, demo_transport=LostReply()), database, request) as client:
        response = client.post('/api/demo/orders', json={'product_id':1,'quantity':2}, expected_status=504, expected_database={'stock':3,'order_count':1,'payment_event_count':0})
    record = response.json()
    assert response.status_code == 504 and record['response'] is None
    assert record['transport_error']['outcome_unknown'] is True
    assert state(database) == (3, 1, 0) and calls == ['/orders']


def test_unreachable_service_does_not_send_mutation(platform_settings, platform_database, database, request):
    def handler(request):
        raise httpx.ConnectError('Controlled disconnected service', request=request)
    settings = platform_settings.model_copy(update={'app_env': 'test'})
    with recorded_client(create_app(settings, platform_database, order_database=database, demo_transport=httpx.MockTransport(handler)), database, request) as client:
        response = create(client, expected_status=503)
    assert response.status_code == 503 and response.json()['transport_error']['outcome_unknown'] is False
    assert state(database) == (5, 0, 0)


def test_request_cannot_choose_an_arbitrary_target(demo_client, database):
    response = demo_client.post('/api/demo/orders', json={'product_id': 1, 'quantity': 2, 'url': 'https://example.com'})
    assert response.status_code == 422 and state(database) == (5, 0, 0)


def test_actual_tcp_gateway_executes_order_api_and_cleans_its_service(platform_settings, platform_database, database, tmp_path, request):
    service = HTTPTestService(database, tmp_path, 'demo-order-gateway-tcp')
    try:
        service.start()
        settings = platform_settings.model_copy(update={'app_env': 'test', 'order_api_base_url': service.base_url})
        with recorded_client(create_app(settings, platform_database, order_database=database), database, request) as client:
            response = create(client)
            record = response.json(); order = record['data']
            assert response.status_code == 201 and record['request']['url'] == service.base_url + '/orders'
            assert pay(client, order).status_code == 200
            assert state(database) == (3, 1, 1)
    finally:
        service.stop()
    assert service.process.poll() is not None and service.record['cleanup_verified'] is True
