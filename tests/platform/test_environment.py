"""运行环境接口验证：真实 PostgreSQL、实际文件读写与本用例的订单 HTTP 服务。

平台表和业务数据均在独立测试 schema 中准备；故障不停止正在使用的服务。
环境就绪仅说明探测条件通过，不作为业务断言或 PostgreSQL 事务验证。
"""
import os
import socket
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import make_url

from probe.config import Settings
from probe.db import Database
from probe.main import create_app as create_order_app
from probe.models import Product, Order
from probe.platform.environment import EnvironmentMonitor
from probe.platform.main import create_app
from probe.platform.manage import migrate
from probe.platform.models import PlatformBase, TestRun as RunRecord, WorkerRecord
from probe.platform.repository import RunRepository
from probe.platform.runner import Worker


pytestmark = pytest.mark.postgres


@pytest.fixture
def ready_environment(platform_database, platform_settings, database):
    # 只在本用例空 schema 内应用真实迁移，检查版本与结构一起就绪的情况。
    PlatformBase.metadata.drop_all(platform_database.engine)
    migrate(platform_database)
    platform_settings.artifacts_dir.mkdir()
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    app = create_order_app(Settings(app_env="test", fault_injection_enabled=False), database)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "Isolated order HTTP service did not start"
    settings = platform_settings.model_copy(update={"order_api_base_url": f"http://127.0.0.1:{port}"})
    yield settings, platform_database, database
    server.should_exit = True
    thread.join(timeout=5)
    listener.close()
    assert not thread.is_alive(), "Isolated HTTP service did not stop"


@contextmanager
def environment_client(setup, *, settings=None, platform=None, test_database=None, worker=False):
    base_settings, base_platform, base_test = setup
    settings = settings or base_settings
    platform = platform or base_platform
    monitor = EnvironmentMonitor(settings, platform, test_database=test_database or base_test)
    embedded = Worker(RunRepository(platform), settings) if worker else None
    try:
        with TestClient(create_app(settings, platform, order_database=base_test, worker=embedded, environment_monitor=monitor)) as client:
            yield client
    finally:
        monitor.close()


def checks(data):
    return {check["id"]: check for check in data["checks"]}


def test_unavailable_platform_test_database_only_blocks_demo_suite(ready_environment):
    settings, _, _ = ready_environment
    url = make_url(settings.platform_test_database_url).set(port=1).render_as_string(hide_password=False)
    settings = settings.model_copy(update={'platform_test_database_url':url})
    with environment_client(ready_environment,settings=settings,worker=True) as client:
        document=client.get('/api/environment').json()
        assert checks(document)['platform_test_database']['status']=='unavailable'
        suites={suite['id']:suite for suite in document['suites']}
        assert suites['demo_orders']['blocked_by']==['platform_test_database']
        assert suites['demo_orders']['requires_platform_test'] is True
        assert suites['stage1']['execution_ready'] and suites['contract']['execution_ready']


def test_ready_uses_real_database_http_and_directory_without_creating_tasks(ready_environment):
    settings, platform, test = ready_environment
    with environment_client(ready_environment, worker=True) as client:
        response = client.get('/api/environment', headers={'X-Request-ID': 'environment-ready-001'})
        assert response.status_code == 200
        data = response.json()
        assert response.headers['X-Request-ID'] == data['request_id'] == 'environment-ready-001'
        assert data['status'] == 'ready' and data['can_submit']
        assert all(check['status'] == 'ok' for check in data['checks'])
        values = checks(data)
        assert values['platform_database']['details']['database'] == 'probe_platform_test'
        assert values['platform_database']['details']['schema'] == platform.schema
        assert values['platform_database']['details']['migration_versions'] == ['0004_rerun_source']
        assert values['test_database']['details']['can_create_schema'] is True
        assert values['test_database']['details']['database'] == 'probe_test'
        assert values['workers']['details']['online_workers'] == 1
        assert values['workers']['details']['active_runs'] == 0
        assert all(suite['execution_ready'] for suite in data['suites'])
        assert all(check['checked_at'] and check['duration_ms'] >= 0 for check in data['checks'])
        assert not list(settings.artifacts_dir.glob('.probe-environment-*'))
        assert platform.engine.url.password not in response.text
        assert 'postgresql+psycopg://' not in response.text
    with platform.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 0
    with test.sessions() as session:
        assert session.get(Product, 1).stock == 5
        assert session.scalar(select(func.count()).select_from(Order)) == 0


def test_missing_worker_keeps_submission_possible_and_task_queued(ready_environment):
    with environment_client(ready_environment) as client:
        data = client.get('/api/environment').json()
        assert data['status'] == 'waiting' and data['can_submit'] is True
        assert checks(data)['workers']['status'] == 'waiting'
        assert all(not suite['execution_ready'] for suite in data['suites'])
        submitted = client.post('/api/runs', json={'suite_id': 'contract'})
        assert submitted.status_code == 202 and submitted.json()['status'] == 'queued'


def test_expired_heartbeat_is_not_counted_as_online(ready_environment):
    _, platform, _ = ready_environment
    with platform.sessions.begin() as session:
        session.add(WorkerRecord(id=uuid4(), process_id=os.getpid(), status='online'))
        session.flush()
        session.execute(text("UPDATE workers SET heartbeat_at=now()-interval '1 day'"))
    with environment_client(ready_environment) as client:
        worker = checks(client.get('/api/environment').json())['workers']
        assert worker['status'] == 'waiting'
        assert worker['details']['workers'][0]['status'] == 'online'
        assert worker['details']['workers'][0]['online'] is False


def test_platform_database_failure_leaves_worker_unknown_and_other_checks_visible(ready_environment):
    unavailable = Database('postgresql+psycopg://probe:environment-unused@127.0.0.1:1/probe_platform_test')
    try:
        with environment_client(ready_environment, platform=unavailable) as client:
            response = client.get('/api/environment')
            assert response.status_code == 200
            data = response.json(); values = checks(data)
            assert data['status'] == 'blocked' and data['can_submit'] is False
            assert values['platform_database']['status'] == 'unavailable'
            assert values['workers']['status'] == 'unknown'
            assert values['test_database']['status'] == values['artifacts']['status'] == 'ok'
            assert 'environment-unused' not in response.text
    finally:
        unavailable.close()


def test_unavailable_test_database_does_not_block_contract_conditions(ready_environment):
    unavailable = Database('postgresql+psycopg://probe:environment-unused@127.0.0.1:1/probe_test')
    try:
        with environment_client(ready_environment, test_database=unavailable, worker=True) as client:
            data = client.get('/api/environment').json()
            assert data['status'] == 'partial' and data['can_submit']
            suite = {row['id']: row for row in data['suites']}
            assert suite['contract']['execution_ready'] is True
            assert suite['stage1']['execution_ready'] is False
            assert suite['stage1']['blocked_by'] == ['test_database']
    finally:
        unavailable.close()


def test_readonly_test_connection_is_not_ready_for_schema_preparation(ready_environment):
    _, _, test = ready_environment
    readonly = Database(test.engine.url.render_as_string(hide_password=False), schema=test.schema)

    @event.listens_for(readonly.engine, 'connect')
    def set_readonly(connection, record):
        connection.autocommit = True
        connection.execute('SET default_transaction_read_only=on')
        connection.autocommit = False

    try:
        with environment_client(ready_environment, test_database=readonly) as client:
            value = checks(client.get('/api/environment').json())['test_database']
            assert value['status'] == 'unavailable' and value['code'] == 'schema_permissions'
            assert value['details']['can_create_schema'] is True
            assert value['details']['default_read_only'] is True
    finally:
        readonly.close()


@pytest.mark.parametrize('failure', ['missing_column', 'missing_version'])
def test_incomplete_platform_schema_is_reported_without_repairing_it(ready_environment, failure):
    _, platform, _ = ready_environment
    with platform.engine.begin() as connection:
        connection.execute(text('ALTER TABLE test_runs DROP COLUMN rerun_of_id CASCADE' if failure == 'missing_column' else 'DROP TABLE alembic_version'))
    with environment_client(ready_environment) as client:
        value = checks(client.get('/api/environment').json())['platform_database']
        assert value['status'] == 'unavailable'
        if failure == 'missing_column':
            assert value['details']['missing_columns']['test_runs'] == ['rerun_of_id']
        else:
            assert value['code'] == 'migration_mismatch' and value['details']['migration_versions'] == []


@pytest.mark.parametrize('kind', ['missing', 'file'])
def test_artifact_path_problem_is_reported_without_removing_user_file(ready_environment, tmp_path, kind):
    settings, _, _ = ready_environment
    path = tmp_path / 'report-path'
    if kind == 'file':
        path.write_text('must remain unchanged')
    settings = settings.model_copy(update={'artifacts_dir': path})
    with environment_client(ready_environment, settings=settings) as client:
        data = client.get('/api/environment').json()
        assert data['status'] == 'blocked'
        assert checks(data)['artifacts']['code'] == 'directory_missing'
    if kind == 'file':
        assert path.read_text() == 'must remain unchanged'
    else:
        assert not path.exists()


def test_order_api_failure_is_optional_for_testclient_execution(ready_environment):
    settings, _, _ = ready_environment
    settings = settings.model_copy(update={'order_api_base_url': 'http://127.0.0.1:1'})
    with environment_client(ready_environment, settings=settings, worker=True) as client:
        data = client.get('/api/environment').json()
        assert data['status'] == 'ready'
        assert checks(data)['order_api']['status'] == 'unavailable'
        assert all(row['execution_ready'] for row in data['suites'])


def test_directory_without_write_permission_is_not_ready(ready_environment, tmp_path):
    if os.geteuid() == 0:
        pytest.skip('Root bypasses directory permission bits; this permission scenario is unverified')
    settings, _, _ = ready_environment
    path = tmp_path / 'readonly-reports'
    path.mkdir(mode=0o500)
    settings = settings.model_copy(update={'artifacts_dir': path})
    try:
        with environment_client(ready_environment, settings=settings) as client:
            value = checks(client.get('/api/environment').json())['artifacts']
            assert value['status'] == 'unavailable' and value['code'] == 'artifacts_unwritable'
            assert value['details']['error_type'] == 'PermissionError'
        assert not list(path.glob('.probe-environment-*'))
    finally:
        path.chmod(0o700)
