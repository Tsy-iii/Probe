"""启动管理集成验证：真实 HTTP 进程与信号，测试替身不冒充 PostgreSQL 业务验证。"""
import importlib.util
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import httpx
import psutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('probe_dev_runtime', ROOT / 'scripts/dev_runtime.py')
runtime_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runtime_module
spec.loader.exec_module(runtime_module)
Runtime, Service, RuntimeErrorDetail = runtime_module.Runtime, runtime_module.Service, runtime_module.RuntimeErrorDetail
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Development process control requires POSIX')

SERVER = '''
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
import os,signal,subprocess,sys,time
port=int(sys.argv[1]);mode=sys.argv[2]
print(f'boot mode={mode}',flush=True)
if mode=='exit':
    time.sleep(0.2);raise SystemExit(2)
if mode=='ignore':signal.signal(signal.SIGTERM,signal.SIG_IGN)
if mode=='child':
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
    Path('child.pid').write_text(str(child.pid))
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(503 if mode=='unhealthy' else 200);self.end_headers();self.wfile.write(b'ready')
    def log_message(self,*args):pass
server=HTTPServer(('127.0.0.1',port),Handler)
print('listening',flush=True)
server.serve_forever()
'''


def wait_until(check, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    pytest.fail('Process condition did not complete')


def alive(pid):
    try:
        return runtime_module.live(psutil.Process(pid))
    except psutil.NoSuchProcess:
        return False


@pytest.fixture
def factory(tmp_path):
    script = tmp_path / 'server.py'
    script.write_text(SERVER)
    runtimes = []
    external = []

    def make(name='platform', mode='normal', existing=None):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
        command = [sys.executable, str(script), str(port), mode]
        def ready(pid):
            try:
                return httpx.get(f'http://127.0.0.1:{port}', timeout=0.2, trust_env=False).status_code == 200
            except httpx.HTTPError:
                return False
        return Service(name, command, tmp_path, ready, existing or (lambda: None), f'http://127.0.0.1:{port}')

    def managed(services):
        # 正常启动留足调度余量；超时场景在对应测试中显式缩短 deadline。
        runtime = Runtime(tmp_path, services, timeout=5, grace=0.3)
        runtimes.append(runtime)
        return runtime

    def manual(service):
        process = subprocess.Popen(service.command, cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        external.append(process)
        wait_until(lambda: service.ready(process.pid))
        service.existing = lambda: process.pid
        return process

    yield make, managed, manual
    for runtime in runtimes:
        try:
            runtime.stop()
        except RuntimeErrorDetail:
            pass
    for process in external:
        if process.poll() is None:
            process.terminate(); process.wait(timeout=3)


def test_start_twice_reuses_owned_process_and_stop_closes_it(factory):
    make, managed, _ = factory
    service = make(); runtime = managed([service])
    runtime.start(); first = runtime.load()['processes']['platform']['pid']
    assert service.ready(first) and runtime.status()
    runtime.start(); assert runtime.load()['processes']['platform']['pid'] == first
    assert runtime.stop()
    assert not alive(first) and not runtime.load()['processes']


def test_reused_manual_service_remains_alive_after_stop(factory):
    make, managed, manual = factory
    service = make(); process = manual(service); runtime = managed([service])
    runtime.start()
    assert runtime.status() and runtime.load()['processes'] == {}
    assert runtime.stop() and process.poll() is None and service.ready(process.pid)


def test_partial_start_failure_only_rolls_back_new_services(factory):
    make, managed, _ = factory
    first = make(); failing = make('frontend', 'exit'); runtime = managed([first, failing])
    with pytest.raises(RuntimeErrorDetail, match='退出'):
        runtime.start()
    assert runtime.load()['processes'] == {}
    assert not first.ready(None)


def test_readiness_timeout_does_not_leave_service_running(factory):
    make, managed, _ = factory
    service = make(mode='unhealthy'); runtime = managed([service]); runtime.timeout = 0.4
    with pytest.raises(RuntimeErrorDetail, match='等待时间'):
        runtime.start()
    assert runtime.load()['processes'] == {}


def test_wrong_pid_ownership_never_stops_manual_service(factory):
    make, managed, manual = factory
    service = make(); process = manual(service); runtime = managed([service])
    state = runtime.load()
    state['processes']['platform'] = {'pid': process.pid, 'created': psutil.Process(process.pid).create_time()}
    runtime.save(state)
    assert runtime.stop() is False
    assert process.poll() is None and service.ready(process.pid)


def test_changed_birth_time_refuses_signal_even_for_tagged_process(factory):
    make, managed, _ = factory
    runtime = managed([make()]); runtime.start(); original = runtime.load()
    state = runtime.load(); state['processes']['platform']['created'] += 60; runtime.save(state)
    try:
        assert runtime.stop() is False
        assert alive(original['processes']['platform']['pid'])
    finally:
        runtime.save(original)


def test_unhealthy_external_service_is_not_replaced(factory):
    make, managed, manual = factory
    service = make(); process = manual(service); service.ready = lambda pid: False
    runtime = managed([service])
    with pytest.raises(RuntimeErrorDetail, match='重复实例'):
        runtime.start()
    assert process.poll() is None and runtime.load()['processes'] == {}


def test_stop_closes_owned_child_in_another_session(factory):
    make, managed, _ = factory
    service = make(mode='child'); runtime = managed([service]); runtime.start()
    child = int((service.cwd / 'child.pid').read_text())
    assert alive(child)
    assert runtime.stop()
    assert not alive(child)


def test_worker_forced_stop_is_not_reported_as_confirmed_cleanup(factory, capsys):
    make, managed, _ = factory
    service = make('worker', 'ignore'); runtime = managed([service]); runtime.start()
    pid = runtime.load()['processes']['worker']['pid']
    assert runtime.stop() is False
    assert not alive(pid)
    assert '清理状态需' in capsys.readouterr().err


def test_invalid_state_and_concurrent_lock_do_not_start_processes(factory):
    make, managed, _ = factory
    runtime = managed([make()]); runtime.directory.mkdir(parents=True)
    runtime.path.write_text('invalid json')
    with pytest.raises(RuntimeErrorDetail, match='归属记录'):
        runtime.start()
    runtime.path.unlink()
    with runtime.locked():
        with pytest.raises(RuntimeErrorDetail, match='另一个'):
            runtime.start()
    assert runtime.load()['processes'] == {}


def test_interrupted_start_rolls_back_its_process(factory):
    make, managed, _ = factory
    service = make(); normal_ready = service.ready
    def interrupted(pid):
        if normal_ready(pid):
            raise KeyboardInterrupt()
        return False
    service.ready = interrupted; runtime = managed([service])
    with pytest.raises(KeyboardInterrupt):
        runtime.start()
    assert runtime.load()['processes'] == {}


def test_status_without_state_does_not_create_files(factory):
    make, managed, _ = factory
    runtime = managed([make()])
    assert runtime.status() is False
    assert not runtime.directory.exists()


def test_occupied_foreign_port_is_preserved(factory):
    make, managed, manual = factory
    service = make(); process = manual(service)
    port = int(service.address.rsplit(':', 1)[1])
    def foreign():
        assert process.pid in runtime_module.listener_pids(port)
        raise RuntimeErrorDetail('端口属于其他程序')
    service.existing = foreign
    runtime = managed([service])
    with pytest.raises(RuntimeErrorDetail, match='其他程序'):
        runtime.start()
    assert process.poll() is None and service.ready(process.pid)
    assert runtime.load()['processes'] == {}


def test_failed_start_preserves_reused_service(factory):
    make, managed, manual = factory
    reused = make(); process = manual(reused)
    runtime = managed([reused, make('frontend', 'exit')])
    with pytest.raises(RuntimeErrorDetail, match='退出'):
        runtime.start()
    assert process.poll() is None and reused.ready(process.pid)
    assert runtime.load()['processes'] == {}


def test_nan_process_creation_time_is_rejected_before_signals(factory):
    make, managed, manual = factory
    service = make(); process = manual(service); runtime = managed([service])
    state = runtime.load()
    state['processes']['platform'] = {'pid': process.pid, 'created': float('nan')}
    runtime.save(state)
    with pytest.raises(RuntimeErrorDetail, match='归属记录'):
        runtime.stop()
    assert process.poll() is None
