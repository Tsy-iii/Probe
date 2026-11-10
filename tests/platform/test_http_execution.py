"""验证平台实际运行网络用例，以及正常／中断路径清理 HTTP 监听进程。"""
import os
import socket
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from probe.db import Database
from probe.http_testing import read_services, service_processes
from probe.platform.main import create_app
from tests.platform.test_runs import wait_for_terminal
from tests.platform.test_reliability import worker_process, wait_until, finished, alive


pytestmark = pytest.mark.postgres


def assert_clean_services(run, settings):
    assert run['http_services']
    for service in run['http_services']:
        assert service['status'] == 'stopped' and service['cleanup_verified']
        assert not service_processes(service['id'])
        assert not alive(service['process_id'])
        # 自己的监听进程已停止，端口可以重新绑定；不盲杀历史 PID。
        port = int(service['base_url'].rsplit(':', 1)[1])
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('127.0.0.1', port))
    db = Database(settings.order_test_database_url)
    try:
        with db.engine.connect() as connection:
            for attempt in run['attempts']:
                assert connection.scalar(text('SELECT count(*) FROM pg_namespace WHERE nspname=:schema'), {'schema': attempt['isolation_schema']}) == 0
    finally:
        db.close()


def test_network_suite_records_addresses_exceptions_and_server_logs(platform_client, platform_settings):
    selected = [
        'tests/http/test_orders_http.py::test_http_create_query_and_pay',
        'tests/http/test_orders_http.py::test_http_read_timeout_has_no_response',
        'tests/http/test_orders_http.py::test_http_connection_failure_has_no_response',
    ]
    response = platform_client.post('/api/runs', json={'suite_id': 'http_orders', 'case_ids': selected})
    assert response.status_code == 202, response.text
    run = wait_for_terminal(platform_client, response.json()['id'])
    assert run['status'] == 'passed' and run['total_count'] == run['passed_count'] == 3
    assert len(run['http_services']) == 2  # 连接故障只预留未监听端口，不启动服务。
    assert {service['attempt_id'] for service in run['http_services']} == {run['attempts'][0]['id']}
    assert len({service['base_url'] for service in run['http_services']}) == 2
    records = {case['name']: case for case in run['report']['cases']}
    happy = records['test_http_create_query_and_pay']['evidence'][0]
    assert happy['request']['url'].startswith('http://127.0.0.1:')
    # 平台验证保存的请求与证据一致；业务用例的购买数量可由开发者调整。
    # 期望来自请求和执行前数据，不能从响应本身回填期望，也不复制固定的两件金额。
    quantity = happy['request']['body']['quantity']
    unit_price = int(happy['database']['before']['products'][0]['price_cents'])
    assert happy['response']['status_code'] == 201
    assert happy['response']['body']['total_amount_cents'] == unit_price * quantity
    timeout = records['test_http_read_timeout_has_no_response']['evidence'][0]
    assert timeout['response']['status_code'] is None and timeout['transport_error']['type'] == 'ReadTimeout'
    failure = records['test_http_connection_failure_has_no_response']['evidence'][0]
    assert failure['response']['status_code'] is None and failure['transport_error']['phase'] == 'connect'
    assert failure['transport_error']['type'] in ['ConnectError', 'ConnectTimeout']
    first_service = next(service for service in run['http_services'] if 'create_query' in service['case_id'])
    output = platform_client.get(f"/api/runs/{run['id']}/http-services/{first_service['id']}/log")
    assert output.status_code == 200 and 'http-create-001' in output.json()['content']
    assert 'POST' in output.json()['content']
    assert platform_client.get(f"/api/runs/{run['id']}/http-services/{uuid4()}/log").status_code == 404
    exported = platform_client.get(f"/api/runs/{run['id']}/evidence").json()
    assert exported['run']['http_services'] == run['http_services']
    assert_clean_services(run, platform_settings)


@pytest.mark.parametrize('operation', ['normal', 'cancel', 'timeout'])
def test_http_service_lifecycle_for_completed_cancelled_and_timed_out_tasks(platform_client, platform_settings, operation):
    response = platform_client.post('/api/runs', json={'suite_id': 'http_control', 'timeout_seconds': 6 if operation == 'timeout' else 30})
    assert response.status_code == 202
    run_id = response.json()['id']
    if operation == 'cancel':
        def ready():
            value = platform_client.get(f'/api/runs/{run_id}').json()
            return value if value['http_services'] and value['http_services'][0]['status'] == 'ready' else None
        wait_until(ready)
        assert platform_client.post(f'/api/runs/{run_id}/cancel').status_code == 200
    run = finished(platform_client, run_id)
    assert run['status'] == {'normal': 'passed', 'cancel': 'cancelled', 'timeout': 'timed_out'}[operation]
    assert run['http_services'][0]['ready_at']
    assert_clean_services(run, platform_settings)
    if operation == 'normal':
        assert run['total_count'] == run['passed_count'] == 1
    else:
        assert run['total_count'] is None


def test_worker_loss_cleans_old_http_service_before_new_attempt(platform_database, platform_settings):
    with TestClient(create_app(platform_settings, platform_database)) as client:
        with worker_process(platform_settings, 'http-worker-loss') as old_worker:
            accepted = client.post('/api/runs', json={'suite_id': 'http_control', 'timeout_seconds': 30})
            assert accepted.status_code == 202
            run_id = accepted.json()['id']
            def ready():
                run = client.get(f'/api/runs/{run_id}').json()
                return run if run['http_services'] and run['http_services'][0]['ready_at'] else None
            old = wait_until(ready)
            old_worker.kill(); old_worker.wait(timeout=3)
        with worker_process(platform_settings, 'http-worker-recovery'):
            run = finished(client, run_id)
        assert run['status'] == 'passed' and run['attempt_count'] == 2
        assert [attempt['status'] for attempt in run['attempts']] == ['interrupted', 'passed']
        assert len(run['http_services']) == 2
        assert run['http_services'][0]['id'] == old['http_services'][0]['id']
        assert len({service['isolation_schema'] for service in run['http_services']}) == 2
        assert_clean_services(run, platform_settings)


def test_two_workers_http_services_use_distinct_addresses_and_schemas(platform_database, platform_settings):
    with TestClient(create_app(platform_settings, platform_database)) as client:
        with worker_process(platform_settings, 'http-isolation-a'), worker_process(platform_settings, 'http-isolation-b'):
            ids = [client.post('/api/runs', json={'suite_id': 'http_control', 'timeout_seconds': 30}).json()['id'] for _ in range(2)]
            def ready():
                values = [client.get(f'/api/runs/{run_id}').json() for run_id in ids]
                return values if all(run['http_services'] and run['http_services'][0]['ready_at'] for run in values) else None
            active = wait_until(ready)
            assert len({run['http_services'][0]['base_url'] for run in active}) == 2
            assert len({run['http_services'][0]['isolation_schema'] for run in active}) == 2
            for run_id in ids:
                assert client.post(f'/api/runs/{run_id}/cancel').status_code == 200
            runs = [finished(client, run_id) for run_id in ids]
        for run in runs:
            assert run['status'] == 'cancelled'
            assert_clean_services(run, platform_settings)


def test_metadata_write_failure_does_not_prevent_cancel_cleanup(platform_client, platform_settings, monkeypatch):
    response = platform_client.post('/api/runs', json={'suite_id': 'http_control', 'timeout_seconds': 30})
    assert response.status_code == 202
    run_id = response.json()['id']
    def ready():
        run = platform_client.get(f'/api/runs/{run_id}').json()
        return run if run['http_services'] and run['http_services'][0]['ready_at'] else None
    wait_until(ready)
    # 只使本进程 Worker 的辅助清单写回失败；真正的子进程与数据库仍照常清理。
    def cannot_write(*args, **kwargs):
        raise OSError('Controlled metadata write failure')
    monkeypatch.setattr('probe.http_testing.write_services', cannot_write)
    assert platform_client.post(f'/api/runs/{run_id}/cancel').status_code == 200
    run = finished(platform_client, run_id)
    assert run['status'] == 'cancelled'
    assert run['http_service_notice'] and run['http_services'][0]['stopped_at'] is None
    assert_clean_services(run, platform_settings)
