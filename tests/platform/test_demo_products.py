"""商品编辑接口的 PostgreSQL 集成验证；只使用独立测试库与随机 schema。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import time
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text

from probe.db import Database
from probe.models import Order, Product
from probe.platform.main import create_app
from probe.schemas import CreateOrder
from probe.service import create_order

pytestmark = pytest.mark.postgres


def payload(**changes):
    return {"price_cents": "2490", "stock": 8, "expected_price_cents": "1990", "expected_stock": 5, **changes}


@pytest.fixture
def editor_client(platform_settings, platform_database, database):
    settings = platform_settings.model_copy(update={"app_env": "test"})
    with TestClient(create_app(settings, platform_database, order_database=database)) as client:
        yield client


def stored(database):
    with database.sessions() as session:
        product = session.get(Product, 1)
        return product.price_cents, product.stock


def test_edit_price_stock_and_get_latest(editor_client, database):
    assert editor_client.get('/api/data/orders').json()['product_editing']['enabled'] is True
    response = editor_client.patch('/api/data/products/1', json=payload(), headers={'X-Request-ID': 'edit-product-test'})
    assert response.status_code == 200
    assert response.json()['product'] == {'id': 1, 'name': 'Test Notebook', 'price_cents': '2490', 'stock': 8, 'is_active': True}
    assert response.json()['request_id'] == response.headers['x-request-id'] == 'edit-product-test'
    assert stored(database) == (2490, 8)
    assert editor_client.get('/api/data/products/1').json()['product'] == response.json()['product']


def test_zero_stock_is_allowed_and_rejects_order(editor_client, client, database):
    assert editor_client.patch('/api/data/products/1', json=payload(stock=0)).status_code == 200
    assert client.post('/orders', json={'product_id': 1, 'quantity': 1}).status_code == 409
    assert stored(database) == (2490, 0)
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 0


def test_bigint_price_is_transferred_without_float_loss(editor_client, database):
    cents = '9007199254740993'
    response = editor_client.patch('/api/data/products/1', json=payload(price_cents=cents))
    assert response.status_code == 200
    assert response.json()['product']['price_cents'] == cents
    assert stored(database) == (int(cents), 8)


@pytest.mark.parametrize('field,value', [('price_cents','0'), ('price_cents','-1'), ('price_cents','19.90'), ('price_cents','9223372036854775808'), ('price_cents',2490), ('stock',-1), ('stock',1.5), ('stock',True), ('stock',2147483648)])
def test_validation_rejects_one_bad_field_without_writing(editor_client, database, field, value):
    response = editor_client.patch('/api/data/products/1', json=payload(**{field:value}))
    assert response.status_code == 422
    assert response.json()['detail'][0]['loc'] == ['body',field]
    assert stored(database) == (1990,5)


def test_extra_business_fields_are_not_editable(editor_client, database):
    response = editor_client.patch('/api/data/products/1', json={**payload(), 'status':'paid'})
    assert response.status_code == 422
    assert stored(database) == (1990,5)


def test_missing_product_is_clear_404(editor_client, database):
    assert editor_client.patch('/api/data/products/999999', json=payload()).status_code == 404
    assert editor_client.get('/api/data/products/999999').status_code == 404
    assert stored(database) == (1990,5)


@pytest.mark.parametrize('field,value', [('expected_stock',4), ('expected_price_cents','1991')])
def test_stale_price_or_stock_cannot_overwrite(editor_client, database, field, value):
    response = editor_client.patch('/api/data/products/1', json=payload(**{field:value}))
    assert response.status_code == 409
    assert stored(database) == (1990,5)


def test_repeat_old_snapshot_is_rejected_without_duplicate_stock(editor_client, database):
    assert editor_client.patch('/api/data/products/1', json=payload()).status_code == 200
    assert editor_client.patch('/api/data/products/1', json=payload()).status_code == 409
    assert stored(database) == (2490,8)


def test_existing_order_keeps_amount_new_order_uses_new_price(editor_client, client, database):
    old = client.post('/orders', json={'product_id':1,'quantity':2}).json()
    assert old['total_amount_cents'] == 3980
    assert editor_client.patch('/api/data/products/1', json=payload(expected_stock=3)).status_code == 200
    assert client.get(f"/orders/{old['id']}").json()['total_amount_cents'] == 3980
    new = client.post('/orders', json={'product_id':1,'quantity':1}).json()
    assert new['total_amount_cents'] == 2490
    assert client.post('/payments/callback', json={'event_id':'old-price-pay','order_id':old['id'],'amount_cents':3980}).status_code == 200
    assert stored(database) == (2490,7)
    with database.sessions() as session:
        assert session.get(Order,UUID(old['id'])).total_amount_cents == 3980
        assert session.get(Order,UUID(old['id'])).status == 'paid'


def test_two_editors_using_one_snapshot_only_one_wins(editor_client, database):
    barrier = Barrier(2)
    choices = [payload(price_cents='2010',stock=6), payload(price_cents='2020',stock=7)]
    def edit(body):
        barrier.wait(timeout=3)
        return editor_client.patch('/api/data/products/1',json=body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(edit,choices))
    assert sorted(response.status_code for response in responses) == [200,409]
    winner = next(response.json()['product'] for response in responses if response.status_code == 200)
    assert stored(database) == (int(winner['price_cents']),winner['stock'])


def test_waiting_edit_rechecks_stock_after_order_commits(editor_client, database):
    started = Event()
    pid = []
    def before_execute(connection,cursor,statement,parameters,context,executemany):
        if statement.startswith('UPDATE products SET price_cents'):
            pid.append(cursor.connection.info.backend_pid)
            started.set()
    event.listen(database.engine,'before_cursor_execute',before_execute)
    try:
        with database.sessions() as holder:
            holder.scalar(select(Product).where(Product.id==1).with_for_update())
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(editor_client.patch,'/api/data/products/1',json=payload())
                assert started.wait(2), 'Editor did not reach its SQL update'
                with database.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
                    deadline = time.monotonic()+2
                    while time.monotonic()<deadline:
                        waiting = observer.scalar(text('SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid'),{'pid':pid[0]})
                        if waiting == 'Lock':
                            break
                        time.sleep(0.01)
                    assert waiting == 'Lock', 'Test requires real PostgreSQL row-lock contention'
                order = create_order(holder,CreateOrder(product_id=1,quantity=1))
                holder.commit()
                response = future.result(timeout=4)
        assert response.status_code == 409
        assert stored(database) == (1990,4)
        with database.sessions() as session:
            assert session.scalar(select(func.count()).select_from(Order)) == 1
            assert session.get(Order,order.id).total_amount_cents == 1990
    finally:
        event.remove(database.engine,'before_cursor_execute',before_execute)


def test_database_error_after_update_rolls_back_both_fields(editor_client,database):
    with database.engine.begin() as connection:
        connection.execute(text("CREATE FUNCTION reject_edit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'deterministic editor rollback'; RETURN NEW; END $$"))
        connection.execute(text('CREATE TRIGGER reject_edit AFTER UPDATE ON products FOR EACH ROW EXECUTE FUNCTION reject_edit()'))
    assert editor_client.patch('/api/data/products/1',json=payload()).status_code == 503
    assert stored(database) == (1990,5)


@pytest.mark.parametrize('settings_change',[{'app_env':'production'},{'demo_edit_enabled':False}])
def test_disabled_editing_is_enforced_on_server(platform_settings,platform_database,database,settings_change):
    settings = platform_settings.model_copy(update={'app_env':'test',**settings_change})
    with TestClient(create_app(settings,platform_database,order_database=database)) as client:
        assert client.get('/api/data/orders').json()['product_editing']['enabled'] is False
        assert client.patch('/api/data/products/1',json=payload()).status_code == 403
    assert stored(database) == (1990,5)


def test_test_database_public_schema_cannot_be_edited(platform_settings,platform_database,database):
    public = Database(database.engine.url.render_as_string(hide_password=False))
    try:
        settings = platform_settings.model_copy(update={'app_env':'test'})
        with TestClient(create_app(settings,platform_database,order_database=public)) as client:
            assert client.patch('/api/data/products/1',json=payload()).status_code == 403
    finally:
        public.close()
    assert stored(database) == (1990,5)
