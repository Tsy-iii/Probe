# 平台测试的专用 fixture。这里的被测对象是任务执行平台，而非订单服务。
# 使用 probe_platform_test 保存测试任务，每条用例再创建独立 schema。
# pytest 会先加载上层 tests/conftest.py，所以 --require-postgres 选项仍可使用。

import os
import re
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from probe.db import Database
from probe.platform.config import PlatformSettings
from probe.platform.main import create_app
from probe.platform.models import PlatformBase
from probe.platform.repository import RunRepository
from probe.platform.runner import Worker


@pytest.fixture(scope="session")
def platform_admin(request):
    # 只接受明确配置的独立平台测试库，不能对实际任务库 probe_platform 建表／清理。
    url = os.environ.get("PLATFORM_TEST_DATABASE_URL") or dotenv_values(".env").get("PLATFORM_TEST_DATABASE_URL")
    if not url:
        if request.config.getoption("--require-postgres"):
            pytest.fail("PLATFORM_TEST_DATABASE_URL is required", pytrace=False)
        pytest.skip("Separate platform test database is not configured")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+psycopg" or parsed.database != "probe_platform_test":
        pytest.fail("Platform tests require probe_platform_test; refusing to modify this database", pytrace=False)
    database = Database(url)
    try:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        database.close()
        message = f"Platform test PostgreSQL is unavailable ({type(exc).__name__})"
        if request.config.getoption("--require-postgres"):
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    yield database
    # session 级 fixture 在整轮测试结束后释放连接池。
    database.close()


@pytest.fixture
def platform_database(platform_admin):
    # 同一测试库里，用随机 schema 隔开每条用例的任务、尝试与 Worker 记录。
    execution_schema = os.environ.get("PROBE_PLATFORM_TEST_SCHEMA")
    if execution_schema and not re.fullmatch(r"platform_case_[a-f0-9]{32}", execution_schema):
        pytest.fail("Invalid Worker-owned platform test schema", pytrace=False)
    schema = execution_schema or "platform_case_" + uuid4().hex
    if execution_schema is None:
        with platform_admin.engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    database = Database(platform_admin.engine.url.render_as_string(hide_password=False), schema=schema)
    try:
        PlatformBase.metadata.create_all(database.engine)
        # 此时只有表结构，没有历史任务，因此空历史断言不受页面现有记录影响。
        yield database
    finally:
        if execution_schema:
            # 同一任务内每条用例准备全新表；异常中断则由 Worker 删除整个 schema。
            PlatformBase.metadata.drop_all(database.engine)
        database.close()
        if execution_schema is None:
            with platform_admin.engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.fixture
def platform_settings(platform_database, tmp_path):
    # tmp_path 由 pytest 创建；本用例的日志／报告写入独立临时目录。
    # 测试中缩短租约和轮询间隔，以较少等待验证恢复；不是生产性能指标。
    return PlatformSettings(
        database_url=platform_database.engine.url.render_as_string(hide_password=False),
        artifacts_dir=tmp_path / "runs",
        database_schema=platform_database.schema,
        platform_test_database_url=platform_database.engine.url.render_as_string(hide_password=False),
        worker_lease_seconds=2, heartbeat_seconds=0.2, poll_seconds=0.05,
    )


@pytest.fixture
def platform_client(platform_database, platform_settings):
    # 在测试应用生命周期内启动真实 Worker，方便接口用例提交后观察执行结果。
    # 实际部署的 API 与 Worker 是独立进程；这里的 TestClient 不需要 8001 端口。
    worker = Worker(RunRepository(platform_database), platform_settings)
    with TestClient(create_app(platform_settings, platform_database, worker=worker)) as client:
        yield client
