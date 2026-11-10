# pytest 的公共数据准备文件：这里提供 fixture，不是具体业务用例。
# 测试函数参数中的 client、database 等名字，会让 pytest 自动准备对应资源。
# 阅读顺序：postgres_admin（连接测试库）→ database（准备数据）→ client（发请求）。
# 自动化使用 probe_test；Swagger 手动演示使用 probe，二者数据独立。

import os
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from probe.config import Settings
from probe.db import Base, Database
from probe.main import create_app
from probe.models import Product


def pytest_addoption(parser):
    # 加上 --require-postgres 后，缺少数据库会报错，避免将跳过误当作通过。
    parser.addoption("--require-postgres", action="store_true", help="Fail instead of skipping if PostgreSQL is unavailable")


@pytest.fixture(scope="session")
def postgres_admin(request):
    # scope="session" 表示整轮 pytest 复用这个连接管理器，具体数据仍按用例隔离。
    # 优先读取环境变量，其次读取 .env；没有配置时不会改用演示数据库。
    from dotenv import dotenv_values
    url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(".env").get("TEST_DATABASE_URL")
    required = request.config.getoption("--require-postgres")
    if not url:
        if required:
            pytest.fail("TEST_DATABASE_URL is required", pytrace=False)
        pytest.skip("TEST_DATABASE_URL is not configured; PostgreSQL behavior is unverified")
    parsed = make_url(url)
    # 在执行任何建表、删 schema 操作前核验库名，保护手动演示数据。
    app_db = make_url(Settings().database_url).database
    if parsed.drivername != "postgresql+psycopg" or parsed.database != "probe_test" or parsed.database == app_db:
        pytest.fail("Tests require a separate PostgreSQL database named probe_test; refusing to touch this database", pytrace=False)
    db = Database(url)
    try:
        with db.engine.connect() as connection:
            version = connection.scalar(text("SELECT version()"))
        print(f"\nIntegration target: {version}; database=probe_test")
    except SQLAlchemyError as exc:
        db.close()
        message = f"PostgreSQL unavailable ({type(exc).__name__}); no transaction validation performed"
        if required:
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    yield db
    # yield 前是准备，yield 后是所有依赖它的测试结束时的资源释放。
    db.close()


@pytest.fixture
def database(postgres_admin):
    # schema 是数据库中的独立命名空间，用来隔开不同测试的表和数据。
    # 直接运行 pytest 时，每条用例创建 case_UUID；平台执行时使用 Worker
    # 提供的 run_UUID。这里不把所有请求包在一个测试事务中，各请求实际提交，
    # 才能观察多个连接竞争库存、行锁等待和事务回滚的真实行为。
    execution_schema = os.environ.get("PROBE_TEST_SCHEMA")
    if execution_schema and not re.fullmatch(r"run_[a-f0-9]{32}", execution_schema):
        pytest.fail("Invalid execution schema", pytrace=False)
    schema = execution_schema or "case_" + uuid4().hex
    if execution_schema is None:
        with postgres_admin.engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    db = Database(postgres_admin.engine.url.render_as_string(hide_password=False), schema=schema)
    try:
        Base.metadata.create_all(db.engine)
        # 每条订单用例都从同一条件开始：商品 1，单价 1990 分，库存 5。
        # sessions.begin() 正常退出时提交，准备数据出错时回滚。
        with db.sessions.begin() as session:
            session.add(Product(id=1, name="Test Notebook", price_cents=1990, stock=5))
        yield db
    finally:
        # 正常退出时清理；平台的 run_UUID schema 最终由 Worker 删除。
        # 如果进程被 SIGKILL，finally 不会运行，需依靠 Worker 的恢复清理。
        if execution_schema:
            Base.metadata.drop_all(db.engine)
        db.close()
        if execution_schema is None:
            with postgres_admin.engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.fixture
def client(database):
    # 把这条用例的独立数据库注入订单应用。TestClient 在进程内发送 HTTP
    # 请求，不需要启动 8002 服务，也不会操作 Swagger 中的演示订单。
    app = create_app(Settings(app_env="test", fault_injection_enabled=False), database)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def offline_client():
    # 用于参数、健康等无需数据库的契约测试。这里仍配置 PostgreSQL 引擎，
    # 但地址不可连接；被测路径应在访问数据库前返回，不能据此验证事务。
    settings = Settings(
        database_url="postgresql+psycopg://probe:unused@127.0.0.1:1/probe",
        app_env="test", fault_injection_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        yield client
