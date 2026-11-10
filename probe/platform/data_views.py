"""固定表的只读快照：帮助用户区分业务数据、测试数据和平台执行记录。"""
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, load_only

from probe.models import Order, PaymentEvent, Product
from probe.platform.models import RunAttempt, TestRun, WorkerRecord


@contextmanager
def view_session(database):
    # 多张表来自同一时刻的快照；PostgreSQL 本身禁止本事务执行写操作。
    with database.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            with Session(bind=connection) as session:
                yield session


def source_info(session, kind, limit):
    row = session.execute(text("SELECT current_database() AS database,current_schema() AS schema,transaction_timestamp() AS captured_at")).mappings().one()
    return {**row, "kind": kind, "readonly": session.scalar(text("SHOW transaction_read_only")) == "on", "row_limit": limit}


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def fields(row, names):
    return {name: getattr(row, name) for name in names}


def order_data(database, limit=50, focus_product_id=None):
    with view_session(database) as session:
        source = source_info(session, "order_demo", limit)
        counts = {"products": count(session, Product), "orders": count(session, Order), "payment_events": count(session, PaymentEvent)}
        product_states = {"active": session.scalar(select(func.count()).select_from(Product).where(Product.is_active.is_(True))), "deleted": session.scalar(select(func.count()).select_from(Product).where(Product.is_active.is_(False)))}
        statuses = {status: 0 for status in ["pending", "paid", "cancelled"]}
        statuses.update(dict(session.execute(select(Order.status, func.count()).group_by(Order.status)).all()))
        product_query = select(Product).order_by(Product.id).limit(limit)
        if focus_product_id is not None:
            product_query = product_query.where(Product.id == focus_product_id)
        products = [{**fields(product, ["id", "name", "stock", "is_active"]), "price_cents": str(product.price_cents)} for product in session.scalars(product_query)]
        orders = []
        event_count = select(func.count()).select_from(PaymentEvent).where(PaymentEvent.order_id == Order.id).correlate(Order).scalar_subquery()
        for order, name, payments in session.execute(select(Order, Product.name, event_count).join(Product, Product.id == Order.product_id).order_by(Order.created_at.desc(), Order.id).limit(limit)):
            orders.append({**fields(order, ["id", "product_id", "quantity", "status", "created_at", "updated_at"]), "total_amount_cents": str(order.total_amount_cents), "product_name": name, "payment_event_count": payments})
        events = []
        for event, status, product_id in session.execute(select(PaymentEvent, Order.status, Order.product_id).join(Order, Order.id == PaymentEvent.order_id).order_by(PaymentEvent.created_at.desc(), PaymentEvent.event_id).limit(limit)):
            events.append({**fields(event, ["event_id", "order_id", "created_at"]), "amount_cents": str(event.amount_cents), "order_status": status, "product_id": product_id})
        return {"source": source, "counts": counts, "product_status_counts": product_states, "status_counts": statuses, "products": products, "orders": orders, "payment_events": events}


def platform_data(database, lease_seconds, limit=50):
    with view_session(database) as session:
        source = source_info(session, "platform_execution", limit)
        counts = {"runs": count(session, TestRun), "attempts": count(session, RunAttempt), "workers": count(session, WorkerRecord)}
        names = ["id", "suite_id", "status", "created_at", "started_at", "finished_at", "exit_code", "total_count", "passed_count", "failed_count", "error_count", "skipped_count", "attempt_count", "timeout_seconds"]
        # 报告 JSONB 可能较大，这里只读取列表需要的字段。
        runs = [fields(run, names) for run in session.scalars(select(TestRun).options(load_only(*(getattr(TestRun, name) for name in names))).order_by(TestRun.created_at.desc(), TestRun.id).limit(limit))]
        attempt_names = ["id", "run_id", "number", "worker_id", "status", "started_at", "finished_at", "process_id", "isolation_schema", "stop_reason", "cleanup_done", "exit_code"]
        attempts = [fields(attempt, attempt_names) for attempt in session.scalars(select(RunAttempt).options(load_only(*(getattr(RunAttempt, name) for name in attempt_names))).order_by(RunAttempt.started_at.desc(), RunAttempt.id).limit(limit))]
        online = (WorkerRecord.status == "online") & (WorkerRecord.heartbeat_at > source["captured_at"] - timedelta(seconds=lease_seconds))
        workers = [{**fields(worker, ["id", "process_id", "heartbeat_at"]), "last_status": worker.status, "online": bool(is_online)} for worker, is_online in session.execute(select(WorkerRecord, online).order_by(WorkerRecord.heartbeat_at.desc(), WorkerRecord.id).limit(limit))]
        return {"source": source, "counts": counts, "online_workers": session.scalar(select(func.count()).select_from(WorkerRecord).where(online)), "runs": runs, "attempts": attempts, "workers": workers}
