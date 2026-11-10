"""演示商品的受控更新：原值条件更新，避免覆盖同时发生的库存扣减。"""
import os
import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, insert, select, text, update

from probe.models import Product


def editing_capability(settings, database, *, injected=False):
    if not settings.demo_edit_enabled or settings.app_env == "production":
        return {"enabled": False, "reason": "当前环境未开放演示商品编辑"}
    name, schema = database.engine.url.database, database.schema
    demo = name == "probe" and schema in {None, "public"}
    # 测试只能通过应用工厂显式注入的独立 schema 验证写入；不开放 probe_test/public。
    owned_run = False
    try:
        attempt = UUID(os.environ.get("PROBE_ATTEMPT_ID", ""))
        owned_run = (schema == "run_" + attempt.hex == os.environ.get("PROBE_TEST_SCHEMA")
                     and os.environ.get("PROBE_PLATFORM_TEST_SCHEMA") == "platform_case_" + attempt.hex)
    except ValueError:
        pass
    isolated_test = settings.app_env == "test" and injected and name == "probe_test" and (bool(re.fullmatch(r"case_[a-f0-9]{32}", schema or "")) or owned_run)
    if not (demo or isolated_test):
        return {"enabled": False, "reason": "仅允许编辑演示库商品，测试库和平台记录不开放编辑"}
    return {"enabled": True, "can_create": True, "can_delete": True, "can_restore": True, "reason": "仅管理演示商品，订单和平台记录不可直接编辑"}


def product_document(row):
    return {"id": row.id, "name": row.name, "price_cents": str(row.price_cents), "stock": row.stock, "is_active": row.is_active}


def read_product(database, product_id):
    with database.sessions() as session:
        row = session.get(Product, product_id)
        if row is None:
            raise HTTPException(404, "商品不存在")
        return product_document(row)


def update_product(database, product_id, payload):
    with database.sessions.begin() as session:
        row = session.execute(
            update(Product).where(
                Product.id == product_id,
                Product.is_active.is_(True),
                Product.price_cents == int(payload.expected_price_cents),
                Product.stock == payload.expected_stock,
            ).values(price_cents=int(payload.price_cents), stock=payload.stock)
            .returning(Product.id, Product.name, Product.price_cents, Product.stock, Product.is_active)
        ).first()
        if row is None:
            existing = session.get(Product, product_id)
            if existing is None:
                raise HTTPException(404, "商品不存在")
            if not existing.is_active:
                raise HTTPException(409, "商品已删除，请先恢复后再编辑")
            raise HTTPException(409, "商品价格或库存已变化，请重新读取后再保存")
        result = product_document(row)
    # 离开事务上下文并成功提交后，才向前端返回成功。
    return result


def create_product(database, payload):
    with database.sessions.begin() as session:
        # 仅低频新增持短暂表锁：兼容旧演示数据手工指定 ID、序列尚未前进的情况。
        # 库存扣减仍使用行级原子更新；这里不使用 Python 全局锁。
        session.execute(text("LOCK TABLE products IN SHARE ROW EXCLUSIVE MODE"))
        maximum = session.scalar(select(func.max(Product.id))) or 0
        product_id = session.scalar(text("SELECT nextval(pg_get_serial_sequence('products','id'))"))
        if product_id <= maximum:
            session.execute(text("SELECT setval(pg_get_serial_sequence('products','id'),:maximum,true)"), {"maximum": maximum})
            product_id = session.scalar(text("SELECT nextval(pg_get_serial_sequence('products','id'))"))
        if product_id > 2_147_483_647:
            raise HTTPException(409, "商品编号已超出支持范围")
        row = session.execute(insert(Product).values(id=product_id, name=payload.name, price_cents=int(payload.price_cents), stock=payload.stock, is_active=True).returning(Product.id, Product.name, Product.price_cents, Product.stock, Product.is_active)).first()
        result = product_document(row)
    return result


def set_product_active(database, product_id, active):
    with database.sessions.begin() as session:
        row = session.execute(update(Product).where(Product.id == product_id, Product.is_active.is_(not active)).values(is_active=active).returning(Product.id, Product.name, Product.price_cents, Product.stock, Product.is_active)).first()
        if row is None:
            existing = session.get(Product, product_id)
            if existing is None:
                raise HTTPException(404, "商品不存在")
            result = product_document(existing)  # 重复删除／恢复不改价格和库存。
        else:
            result = product_document(row)
    return result
