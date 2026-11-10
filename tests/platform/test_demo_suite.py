"""通过真实 Worker 验证演示订单套件的执行范围、证据和两份测试库清理。"""
import asyncio
import threading
import time
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, text, update

from probe.db import Database
from probe.platform.models import TestRun as RunRecord
from probe.platform.case_catalog import catalog
from probe.platform.config import ROOT
from probe.platform.main import create_app
from probe.platform.repository import RunRepository, TERMINAL
from probe.platform.runner import Worker
from probe.platform.suites import SUITES, Suite

pytestmark = pytest.mark.postgres


def wait_for_terminal(client, run_id, timeout=40):
    # 只观察同一次执行，包含取消／超时；不重新提交、不重试业务请求。
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f'/api/runs/{run_id}')
        assert response.status_code == 200
        run = response.json()
        if run['status'] in TERMINAL:
            return run
        time.sleep(0.05)
    pytest.fail('Demo suite did not finish within its observation window')


def assert_clean(run, settings):
    attempt = run['attempts'][0]
    assert attempt['cleanup_done'] is True
    locations = [(settings.order_test_database_url, attempt['isolation_schema']),
                 (settings.platform_test_database_url, 'platform_case_' + UUID(attempt['id']).hex)]
    for url, schema in locations:
        database = Database(url)
        try:
            with database.engine.connect() as connection:
                assert connection.scalar(text('SELECT count(*) FROM pg_namespace WHERE nspname=:name'), {'name':schema}) == 0
        finally:
            database.close()


@pytest.mark.parametrize('selected', [False, True])
def test_demo_suite_runs_exact_scope_and_retains_evidence(platform_client, platform_settings, selected):
    directory = platform_client.get('/api/suites/demo_orders/cases').json()
    assert directory['count'] == 21
    assert all(case['description'] != '此用例尚未提供业务说明，请按源码阅读。' for case in directory['cases'])
    payload = {'suite_id':'demo_orders', 'catalog_revision':directory['catalog_revision']}
    if selected:
        payload['case_ids'] = [case['id'] for case in directory['cases'] if case['function']=='test_cancel_and_repeat_restore_stock_once']
    accepted = platform_client.post('/api/runs', json=payload)
    assert accepted.status_code == 202
    run = wait_for_terminal(platform_client, accepted.json()['id'], timeout=40)
    assert run['status'] == 'passed', run
    assert run['total_count'] == run['passed_count'] == (1 if selected else 21)
    assert run['skipped_count'] == run['failed_count'] == run['error_count'] == 0
    assert all(case['evidence'] for case in run['report']['cases'])
    assert all(item['expected']==item['actual'] for case in run['report']['cases'] for evidence in case['evidence'] for item in evidence['checks'])
    if selected:
        case = run['report']['cases'][0]
        cancels = [record for record in case['evidence'] if record['request']['path'].endswith('/cancel')]
        assert len(cancels)==2
        assert cancels[0]['database']['before']['products'][0]['stock']==3
        assert cancels[0]['database']['after']['products'][0]['stock']==5
        assert cancels[1]['database']['before']==cancels[1]['database']['after']
    assert_clean(run, platform_settings)
    assert platform_client.get(f"/api/runs/{run['id']}/evidence").status_code==200
    assert platform_client.get(f"/api/runs/{run['id']}/junit").status_code==200


@pytest.mark.parametrize('stop', ['cancel', 'spawn_error'])
def test_demo_suite_cleans_both_schemas_on_stop(platform_settings, platform_database, monkeypatch, stop):
    worker = Worker(RunRepository(platform_database), platform_settings)
    allocated = threading.Event(); release = threading.Event()
    original = worker._spawn
    async def controlled(*args, **kwargs):
        allocated.set()
        if stop=='spawn_error':
            raise RuntimeError('Controlled failure after both schemas were created')
        assert await asyncio.to_thread(release.wait, 5)
        return await original(*args, **kwargs)
    monkeypatch.setattr(worker, '_spawn', controlled)
    with TestClient(create_app(platform_settings, platform_database, worker=worker)) as client:
        selected = [case['id'] for case in catalog.suite('demo_orders')['cases'] if case['function']=='test_cancel_and_repeat_restore_stock_once']
        response = client.post('/api/runs', json={'suite_id':'demo_orders','case_ids':selected})
        assert allocated.wait(5)
        if stop=='cancel':
            run = client.get(f"/api/runs/{response.json()['id']}").json()
            marker = worker.directory(UUID(run['id']), UUID(run['attempts'][0]['id']))/'platform-test-schema'
            assert marker.exists()
            client.post(f"/api/runs/{run['id']}/cancel")
            release.set()
        run = wait_for_terminal(client, response.json()['id'])
        assert run['status']==('cancelled' if stop=='cancel' else 'error')
        assert_clean(run, platform_settings)


def test_demo_failure_keeps_http_and_database_evidence(platform_client, platform_settings, monkeypatch):
    # 创建本用例自己的临时模块，故意写错金额断言；原始业务测试文件不修改。
    target = ROOT/'tests/platform'/f'_demo_failure_{uuid4().hex}.py'
    source = (ROOT/'tests/platform/test_demo_orders.py').read_text()
    source = source.replace("order['total_amount_cents'] == '3980'", "order['total_amount_cents'] == '3981'", 1)
    name = 'test_create_query_pay_and_repeat_keep_real_status_and_request_id'
    try:
        target.write_text(source)
        relative = str(target.relative_to(ROOT))+'::'+name
        monkeypatch.setitem(SUITES,'demo_orders',Suite('demo_orders','受控断言失败','仅验证失败证据保留',(relative,),True,requires_platform_test=True))
        response = platform_client.post('/api/runs',json={'suite_id':'demo_orders'})
        run = wait_for_terminal(platform_client,response.json()['id'],timeout=30)
        assert run['status']=='failed' and run['failed_count']==1
        case=run['report']['cases'][0]
        assert '3981' in case['details'] and '3980' in case['details']
        creation=next(item for item in case['evidence'] if item['request']['path']=='/api/demo/orders')
        assert creation['response']['status_code']==201
        assert creation['response']['body']['data']['total_amount_cents']=='3980'
        assert creation['database']['before']['products'][0]['stock']==5
        assert creation['database']['after']['products'][0]['stock']==3
        assert_clean(run,platform_settings)
    finally:
        target.unlink(missing_ok=True)


@pytest.mark.parametrize('mismatch', ['no_attempt', 'different_order_schema', 'different_platform_schema'])
def test_unowned_run_schema_is_not_opened_for_demo_writes(platform_settings, platform_database, database, monkeypatch, mismatch):
    attempt = uuid4()
    schema = 'run_' + attempt.hex
    monkeypatch.setenv('PROBE_ATTEMPT_ID', str(attempt))
    monkeypatch.setenv('PROBE_TEST_SCHEMA', schema)
    monkeypatch.setenv('PROBE_PLATFORM_TEST_SCHEMA', 'platform_case_' + attempt.hex)
    if mismatch=='no_attempt':
        monkeypatch.delenv('PROBE_ATTEMPT_ID')
    elif mismatch=='different_order_schema':
        monkeypatch.setenv('PROBE_TEST_SCHEMA','run_' + uuid4().hex)
    else:
        monkeypatch.setenv('PROBE_PLATFORM_TEST_SCHEMA','platform_case_' + uuid4().hex)
    unowned=Database(database.engine.url.render_as_string(hide_password=False),schema=schema)
    try:
        settings=platform_settings.model_copy(update={'app_env':'test'})
        with TestClient(create_app(settings,platform_database,order_database=unowned)) as client:
            response=client.post('/api/demo/orders',json={'product_id':1,'quantity':1})
            assert response.status_code==403
    finally:
        unowned.close()


def test_replacement_worker_recovers_both_schema_ownership(platform_settings, platform_database):
    repository=RunRepository(platform_database)
    accepted=repository.create('demo_orders',max_attempts=1)
    old=repository.claim(uuid4(),platform_settings.worker_lease_seconds,{'demo_orders'})
    attempt=old.attempts[0]
    replacement=Worker(repository,platform_settings)
    directory=replacement.directory(old.id,attempt.id)
    directory.mkdir(parents=True)
    (directory/'platform-test-schema').write_text('platform_case_' + attempt.id.hex)
    try:
        replacement._schema(attempt.isolation_schema,True)
        schema=replacement._platform_test_schema(attempt.id,True)
        database=Database(platform_settings.platform_test_database_url)
        try:
            with database.engine.begin() as connection:
                connection.execute(text(f'CREATE TABLE "{schema}".recovery_probe (value integer)'))
                connection.execute(text(f'INSERT INTO "{schema}".recovery_probe VALUES (1)'))
        finally:
            database.close()
        with platform_database.sessions.begin() as session:
            session.execute(update(RunRecord).where(RunRecord.id==old.id).values(lease_until=func.now()-timedelta(seconds=3)))
        recovered=repository.claim_recovery(replacement.id,platform_settings.worker_lease_seconds)
        assert recovered.id==UUID(str(accepted.id))
        asyncio.run(replacement._recover(recovered))
        run=repository.get(old.id).model_dump(mode='json')
        assert run['status']=='error' and run['attempts'][0]['status']=='interrupted'
        assert_clean(run,platform_settings)
    finally:
        replacement._schema(attempt.isolation_schema,False)
        replacement._platform_test_schema(attempt.id,False)
