"""商品管理只在真实 PostgreSQL 测试 schema 验证，绝不新增或删除演示商品。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from probe.models import Order, Product
from probe.platform.main import create_app

pytestmark = pytest.mark.postgres

@pytest.fixture
def manager(platform_settings,platform_database,database):
    settings=platform_settings.model_copy(update={'app_env':'test'})
    with TestClient(create_app(settings,platform_database,order_database=database)) as client:
        yield client


def body(**values):
    return {'name':'New Product','price_cents':'2999','stock':7,**values}


def test_create_repairs_sequence_without_overwriting_manual_seed(manager,database):
    with database.sessions.begin() as session:
        session.add(Product(id=2,name='Manual second',price_cents=500,stock=3))
    response=manager.post('/api/data/products',json=body(name='  Created  '))
    assert response.status_code==201
    product=response.json()['product']
    assert product['id'] not in [1,2]
    assert (product['name'],product['price_cents'],product['stock'],product['is_active'])==('Created','2999',7,True)
    with database.sessions() as session:
        assert session.get(Product,1).price_cents==1990
        assert session.get(Product,2).stock==3
        assert session.get(Product,product['id']).name=='Created'


@pytest.mark.parametrize('field,value',[('name','  '),('name','x'*101),('price_cents','0'),('stock',-1),('price_cents','9223372036854775808')])
def test_invalid_create_changes_only_one_field(manager,database,field,value):
    response=manager.post('/api/data/products',json=body(**{field:value}))
    assert response.status_code==422
    with database.sessions() as session:
        assert len(session.scalars(select(Product)).all())==1


def test_two_creators_get_unique_database_ids(manager,database):
    start=Barrier(2)
    def create(name):
        start.wait(timeout=3)
        return manager.post('/api/data/products',json=body(name=name))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(create,['First','Second']))
    assert all(result.status_code==201 for result in results)
    ids=[result.json()['product']['id'] for result in results]
    assert len(set(ids))==2 and 1 not in ids
    with database.sessions() as session:
        assert len(session.scalars(select(Product)).all())==3


def test_soft_delete_preserves_order_payment_and_inventory(manager,client,database):
    pending=client.post('/orders',json={'product_id':1,'quantity':2}).json()
    before=manager.get('/api/data/orders').json()
    removed=manager.delete('/api/data/products/1')
    assert removed.status_code==200 and removed.json()['product']['is_active'] is False
    rejected=client.post('/orders',json={'product_id':1,'quantity':1})
    assert rejected.status_code==409 and rejected.json()['error']['code']=='product_inactive'
    assert client.get(f"/orders/{pending['id']}").status_code==200
    paid=client.post('/payments/callback',json={'event_id':'removed-old-order','order_id':pending['id'],'amount_cents':3980})
    assert paid.status_code==200 and paid.json()['order']['status']=='paid'
    with database.sessions() as session:
        product=session.get(Product,1)
        assert product.stock==3 and product.price_cents==1990
        assert session.get(Order,UUID(pending['id'])).total_amount_cents==3980
    after=manager.get('/api/data/orders').json()
    assert after['counts']['products']==before['counts']['products']==1
    assert after['product_status_counts']=={'active':0,'deleted':1}


def test_repeat_delete_restore_is_idempotent_and_restored_order_can_be_created(manager,client,database):
    first=manager.delete('/api/data/products/1').json()['product']
    assert manager.delete('/api/data/products/1').json()['product']==first
    restored=manager.post('/api/data/products/1/restore')
    assert restored.status_code==200 and restored.json()['product']['is_active'] is True
    assert manager.post('/api/data/products/1/restore').json()['product']==restored.json()['product']
    assert client.post('/orders',json={'product_id':1,'quantity':1}).status_code==201
    with database.sessions() as session:
        assert session.get(Product,1).stock==4


def test_restore_keeps_inventory_released_by_cancel_during_deletion(manager,client,database):
    order=client.post('/orders',json={'product_id':1,'quantity':2}).json()
    assert manager.delete('/api/data/products/1').json()['product']['stock']==3
    assert client.post(f"/orders/{order['id']}/cancel").status_code==200
    restored=manager.post('/api/data/products/1/restore').json()['product']
    assert restored['stock']==5 and restored['price_cents']=='1990'
    assert restored['is_active'] is True


def test_removed_product_cannot_be_edited_until_restored(manager,database):
    assert manager.delete('/api/data/products/1').status_code==200
    patch={'price_cents':'2490','stock':9,'expected_price_cents':'1990','expected_stock':5}
    assert manager.patch('/api/data/products/1',json=patch).status_code==409
    assert manager.post('/api/data/products/1/restore').status_code==200
    assert manager.patch('/api/data/products/1',json=patch).status_code==200
    # 再次恢复已在售商品不回滚后来修改的价格与库存。
    again=manager.post('/api/data/products/1/restore').json()['product']
    assert (again['price_cents'],again['stock'])==('2490',9)


@pytest.mark.parametrize('method,path',[('delete','/api/data/products/999999'),('post','/api/data/products/999999/restore')])
def test_missing_product_returns_404(manager,method,path):
    assert getattr(manager,method)(path).status_code==404


def test_operations_disabled_in_production(platform_settings,platform_database,database):
    settings=platform_settings.model_copy(update={'app_env':'production'})
    with TestClient(create_app(settings,platform_database,order_database=database)) as client:
        assert client.post('/api/data/products',json=body()).status_code==403
        assert client.delete('/api/data/products/1').status_code==403
        assert client.post('/api/data/products/1/restore').status_code==403
    with database.sessions() as session:
        assert session.get(Product,1).is_active is True


def test_focus_product_remains_visible_beyond_row_limit(manager,database):
    created=manager.post('/api/data/products',json=body()).json()['product']
    result=manager.get(f"/api/data/orders?limit=1&focus_product_id={created['id']}").json()
    assert result['counts']['products']==2
    assert len(result['products'])==1 and result['products'][0]['id']==created['id']
    assert result['product_status_counts']=={'active':2,'deleted':0}


def test_create_error_rolls_back_and_does_not_remove_existing_product(manager,database):
    with database.engine.begin() as connection:
        connection.execute(text("CREATE FUNCTION reject_new() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'deterministic create rollback'; RETURN NEW; END $$"))
        connection.execute(text('CREATE TRIGGER reject_new AFTER INSERT ON products FOR EACH ROW EXECUTE FUNCTION reject_new()'))
    assert manager.post('/api/data/products',json=body()).status_code==503
    with database.sessions() as session:
        rows=session.scalars(select(Product)).all()
        assert len(rows)==1 and rows[0].is_active is True and rows[0].stock==5
