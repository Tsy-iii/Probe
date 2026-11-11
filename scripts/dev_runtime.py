"""本地服务管理；归属来自 session 标记和进程创建时间，不接管手动启动的服务。"""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NAMES = ('order', 'platform', 'worker', 'frontend')
LABELS = {'order': '订单 API', 'platform': '平台 API', 'worker': 'Worker', 'frontend': '前端'}
MARK_ROOT, MARK_SESSION, MARK_SERVICE = 'PROBE_DEV_ROOT', 'PROBE_DEV_SESSION', 'PROBE_DEV_SERVICE'


class RuntimeErrorDetail(Exception):
    pass


def live(process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


@dataclass
class Service:
    name: str
    command: list[str]
    cwd: Path
    ready: object
    existing: object
    address: str = '—'


class Runtime:
    def __init__(self, root, services, *, timeout=10, grace=8, environment=None):
        self.root = Path(root).resolve()
        self.services = {service.name: service for service in services}
        if not self.services or not set(self.services) <= set(NAMES):
            raise ValueError('Unknown development service')
        self.directory = self.root / '.local' / 'dev-runtime'
        self.path = self.directory / 'state.json'
        self.timeout, self.grace = timeout, grace
        self.environment = environment if environment is not None else os.environ.copy()

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / 'control.lock').open('a') as handle:
            os.fchmod(handle.fileno(), 0o600)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeErrorDetail('另一个 start/stop 正在执行，请稍后再试。')
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def load(self):
        if not self.path.exists():
            return {'version': 1, 'root': str(self.root), 'session': uuid4().hex, 'processes': {}}
        try:
            state = json.loads(self.path.read_text())
            if state['version'] != 1 or state['root'] != str(self.root):
                raise ValueError('State version or repository mismatch')
            if not re.fullmatch(r'[a-f0-9]{32}', state['session']):
                raise ValueError('Invalid session')
            if not isinstance(state['processes'], dict) or not set(state['processes']) <= set(NAMES):
                raise ValueError('Invalid service records')
            for record in state['processes'].values():
                if type(record['pid']) is not int or record['pid'] <= 1:
                    raise ValueError('Invalid process ID')
                if type(record['created']) not in (float, int) or not math.isfinite(record['created']) or record['created'] <= 0:
                    raise ValueError('Invalid process creation time')
            return state
        except (OSError, ValueError, KeyError, TypeError, AssertionError):
            raise RuntimeErrorDetail('服务归属记录无法验证，未发送停止信号。请检查 .local/dev-runtime/state.json。')

    def save(self, state):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / 'state.tmp'
        with temporary.open('w') as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(state, handle)
        temporary.replace(self.path)

    def owned(self, process, state, name):
        if not live(process) or process.pid == os.getpid():
            return False
        try:
            env = process.environ()
            return (env.get(MARK_ROOT) == str(self.root) and env.get(MARK_SESSION) == state['session']
                    and env.get(MARK_SERVICE) == name)
        except psutil.Error:
            return False

    def leader(self, state, name):
        record = state['processes'].get(name)
        if not record:
            return None
        try:
            process = psutil.Process(record['pid'])
            if abs(process.create_time() - record['created']) < 0.01 and self.owned(process, state, name):
                return process
        except psutil.Error:
            pass
        return None

    def descendants(self, state, name):
        # 包括 Vite 的构建子进程，以及 Worker 启动的新会话执行进程。
        return [process for process in psutil.process_iter() if self.owned(process, state, name)]

    def inspect(self, state, name):
        service = self.services[name]
        process = self.leader(state, name)
        if process:
            return ('managed' if service.ready(process.pid) else 'unhealthy', process.pid)
        if self.descendants(state, name):
            return 'orphaned', None
        if name in state['processes']:
            try:
                if live(psutil.Process(state['processes'][name]['pid'])):
                    return 'unverified', None
            except psutil.Error:
                pass
        pid = service.existing()
        if pid is not None:
            return ('reused' if service.ready(pid) else 'unhealthy_external', pid)
        return 'stopped', None

    def start(self):
        with self.locked():
            state = self.load()
            # 先确认所有已有服务的身份与状态，避免启动一半后才发现端口属于其他程序。
            existing = {name: self.inspect(state, name) for name in self.services}
            for name, (status, _) in existing.items():
                if status not in {'managed', 'reused', 'stopped'}:
                    raise RuntimeErrorDetail(f'{LABELS[name]} 状态为 {status}，未启动重复实例。请查看 status 和日志。')
            created = []
            self.save(state)  # session 先落盘，启动中断后也可扫描归属标记。
            try:
                for name, service in self.services.items():
                    status, pid = existing[name]
                    if status in {'managed', 'reused'}:
                        print(f'{LABELS[name]}：' + ('脚本管理，已就绪' if status == 'managed' else '复用手动服务，stop 不会关闭它'))
                        continue
                    env = self.environment.copy()
                    env.update({MARK_ROOT: str(self.root), MARK_SESSION: state['session'], MARK_SERVICE: name})
                    with (self.directory / f'{name}.log').open('ab') as output:
                        process = subprocess.Popen(service.command, cwd=service.cwd, env=env, stdin=subprocess.DEVNULL,
                                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                    created.append(name)
                    state['processes'][name] = {'pid': process.pid, 'created': psutil.Process(process.pid).create_time()}
                    self.save(state)
                    deadline = time.monotonic() + self.timeout
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise RuntimeErrorDetail(f'{LABELS[name]} 启动后退出，日志：.local/dev-runtime/{name}.log')
                        if service.ready(process.pid):
                            print(f'{LABELS[name]}：已启动并就绪')
                            break
                        time.sleep(0.1)
                    else:
                        raise RuntimeErrorDetail(f'{LABELS[name]} 未在等待时间内就绪，日志：.local/dev-runtime/{name}.log')
            except BaseException:
                for name in reversed(created):
                    self.stop_service(state, name)
                self.save(state)
                raise

    def stop_service(self, state, name):
        record = state['processes'].get(name)
        if not record and not self.descendants(state, name):
            return True
        process = self.leader(state, name)
        if record and not process:
            try:
                if live(psutil.Process(record['pid'])):
                    print(f'{LABELS[name]}：PID 归属或创建时间不匹配，保留该进程。', file=sys.stderr)
                    return False
            except psutil.Error:
                pass
        if process:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
        else:
            for child in self.descendants(state, name):
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
        deadline = time.monotonic() + self.grace
        while time.monotonic() < deadline and self.descendants(state, name):
            time.sleep(0.1)
        survivors = self.descendants(state, name)
        forced = bool(survivors)
        for child in survivors:
            try:
                child.kill()  # psutil 在发送信号时再次检查进程身份；不直接盲杀进程组。
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(survivors, timeout=2)
        if self.descendants(state, name):
            print(f'{LABELS[name]}：仍有自启进程，保留归属记录供再次检查。', file=sys.stderr)
            return False
        state['processes'].pop(name, None)
        if forced and name == 'worker':
            print('Worker 被强制停止；测试数据清理状态需在重启后的恢复流程中核对。', file=sys.stderr)
            return False
        return True

    def stop(self):
        with self.locked():
            state = self.load()
            okay = True
            for name in ('worker', 'frontend', 'platform', 'order'):
                if name in self.services:
                    okay = self.stop_service(state, name) and okay
            self.save(state)
            print('脚本自启服务已处理；手动服务、PostgreSQL 和数据保持原样。')
            return okay

    def status(self):
        state = self.load()
        labels = {'managed': '脚本管理／就绪', 'reused': '手动启动／就绪', 'stopped': '未运行', 'unhealthy': '自启但未就绪',
                  'unhealthy_external': '手动服务未就绪', 'orphaned': '有自启残留进程', 'unverified': '归属无法验证'}
        rows = []
        for name, service in self.services.items():
            status, _ = self.inspect(state, name)
            print(f'{LABELS[name]}：{labels[status]}  {service.address}')
            rows.append(status)
        print('日志目录：.local/dev-runtime/；状态查询不修改服务或数据。')
        return all(status in {'managed', 'reused'} for status in rows)


def listener_pids(port):
    with socket.socket() as connection:
        connection.settimeout(0.2)
        if connection.connect_ex(('127.0.0.1', port)) != 0:
            return []
    try:
        pids = [connection.pid for connection in psutil.net_connections(kind='tcp')
                if connection.status == psutil.CONN_LISTEN and connection.laddr.port == port and connection.pid]
        if not pids:
            raise RuntimeErrorDetail(f'端口 {port} 已占用，但无法确认进程身份。')
        return pids
    except psutil.AccessDenied:
        binary = shutil.which('lsof')
        if not binary:
            raise RuntimeErrorDetail(f'端口 {port} 已占用，缺少 lsof，无法确认服务归属。')
        result = subprocess.run([binary, '-nP', '-t', f'-iTCP:{port}', '-sTCP:LISTEN'], capture_output=True, text=True)
        pids = [int(value) for value in result.stdout.split() if value.isdigit()]
        if not pids:
            raise RuntimeErrorDetail(f'端口 {port} 已占用，但无法读取归属。')
        return list(set(pids))


def matching_process(process, name):
    try:
        command = process.cmdline()
        expected = ROOT / 'frontend' if name == 'frontend' else ROOT
        if Path(process.cwd()).resolve() != expected:
            return False
        if name == 'frontend':
            return any('vite' in argument for argument in command)
        module = {'order': 'probe.main:app', 'platform': 'probe.platform.main:app', 'worker': 'probe.platform.runner'}[name]
        return module in command
    except psutil.Error:
        return False


def development_port(environment, name, default):
    value = environment.get(name, str(default))
    if not re.fullmatch(r'[0-9]{1,5}', value) or not 1 <= int(value) <= 65535:
        raise RuntimeErrorDetail(f'{name} must be an integer port between 1 and 65535.')
    return int(value)


def configure():
    from dotenv import dotenv_values
    from sqlalchemy import func, inspect, select, text
    from sqlalchemy.engine import make_url
    from probe.config import Settings
    from probe.db import Database
    from probe.models import Product, Order, PaymentEvent
    from probe.platform.config import PlatformSettings
    from probe.platform.environment import EnvironmentMonitor
    from probe.platform.models import WorkerRecord

    env = {key: value for key, value in dotenv_values(ROOT / '.env').items() if value is not None}
    env.update(os.environ)
    order, platform = Settings(), PlatformSettings()
    platform_port = development_port(env, 'PROBE_DEV_PLATFORM_PORT', 8001)
    frontend_port = development_port(env, 'PROBE_DEV_FRONTEND_PORT', 5174)
    ports = [int(platform.order_api_base_url.rsplit(':', 1)[1]), platform_port, frontend_port]
    if len(set(ports)) != 3 or 5173 in ports:
        raise RuntimeErrorDetail('Application ports must be distinct; port 5173 is reserved for Discovery.')
    database = Database(platform.database_url, schema=platform.database_schema)
    monitor = EnvironmentMonitor(platform, database)
    demo = Database(order.database_url)

    def close():
        monitor.close(); database.close(); demo.close()

    def preflight():
        if order.app_env != 'development' or platform.app_env != 'development':
            raise RuntimeErrorDetail('此启动器只用于 APP_ENV=development。')
        if not (ROOT / '.env').exists():
            raise RuntimeErrorDetail('缺少 .env，请按 README 复制 .env.example 并配置数据库。')
        if make_url(order.database_url).database != 'probe' or make_url(platform.database_url).database != 'probe_platform' or platform.database_schema:
            raise RuntimeErrorDetail('本地启动必须使用 probe 与 probe_platform/public，不接管测试空间。')
        if not shutil.which('node') or not (ROOT / 'frontend/node_modules/vite/bin/vite.js').exists():
            raise RuntimeErrorDetail('缺少 Node.js 或前端依赖，请安装 Node.js 并在 frontend 执行 npm ci。')
        for check in (monitor.platform_database(), monitor.order_test_database(), monitor.platform_regression_database()):
            if check['status'] != 'ok':
                raise RuntimeErrorDetail(check['message'] + ' ' + check['action'])
        try:
            with demo.engine.connect() as connection:
                connection.execute(text('SET TRANSACTION READ ONLY'))
                inspector = inspect(connection)
                for model in (Product, Order, PaymentEvent):
                    columns = {column['name'] for column in inspector.get_columns(model.__tablename__)}
                    if not set(model.__table__.columns.keys()) <= columns:
                        raise RuntimeErrorDetail('订单表结构未就绪，请按 README 初始化或执行订单迁移。')
        except RuntimeErrorDetail:
            raise
        except Exception:
            raise RuntimeErrorDetail('订单数据库无法读取，请按 README 检查连接和初始化。')

    def worker_ready(pid):
        try:
            with database.engine.connect() as connection:
                connection.execute(text('SET TRANSACTION READ ONLY'))
                return bool(connection.scalar(select(WorkerRecord.id).where(WorkerRecord.process_id == pid,
                    WorkerRecord.status == 'online', WorkerRecord.heartbeat_at > func.now()-timedelta(seconds=platform.worker_lease_seconds)).limit(1)))
        except Exception:
            return False

    def http_ready(base, name):
        try:
            with httpx.Client(timeout=1, trust_env=False, follow_redirects=False) as client:
                if name == 'frontend':
                    result = client.get(base)
                    return result.status_code == 200 and 'Probe' in result.text
                result = client.get(base + '/health/ready')
                if result.status_code != 200 or result.json().get('status') != 'ready':
                    return False
                if name == 'order':
                    return result.json().get('database_name') == 'probe' and result.json().get('schema') == 'public' and result.json().get('app_env') == 'development'
                source = client.get(base + '/api/data/platform?limit=1').json().get('source', {})
                return client.get(base + '/health').json().get('service') == 'probe-platform' and source.get('database') == 'probe_platform' and source.get('schema') == 'public'
        except (httpx.HTTPError, ValueError):
            return False

    addresses = {'order': platform.order_api_base_url, 'platform': f'http://127.0.0.1:{platform_port}', 'frontend': f'http://127.0.0.1:{frontend_port}'}
    commands = {'order': [sys.executable, '-m', 'uvicorn', 'probe.main:app', '--host', '127.0.0.1', '--port', addresses['order'].rsplit(':', 1)[1]],
                'platform': [sys.executable, '-m', 'uvicorn', 'probe.platform.main:app', '--host', '127.0.0.1', '--port', str(platform_port)],
                'worker': [sys.executable, '-m', 'probe.platform.runner'],
                # npm 会改写自身进程信息；直接启动 Vite，让归属标记可持续核验。
                'frontend': [shutil.which('node') or 'node', str(ROOT / 'frontend/node_modules/vite/bin/vite.js'),
                             '--host', '127.0.0.1', '--port', str(frontend_port), '--strictPort', '--configLoader', 'runner']}
    services = []
    for name in NAMES:
        def existing(name=name):
            if name == 'worker':
                candidates = [process.pid for process in psutil.process_iter() if matching_process(process, name)]
                return next((pid for pid in candidates if worker_ready(pid)), candidates[0] if candidates else None)
            port = int(addresses[name].rsplit(':', 1)[1])
            pids = listener_pids(port)
            if not pids:
                return None
            for pid in pids:
                try:
                    if matching_process(psutil.Process(pid), name):
                        return pid
                except psutil.Error:
                    pass
            raise RuntimeErrorDetail(f'端口 {port} 属于其他程序，未启动或停止它。')
        ready = worker_ready if name == 'worker' else lambda pid, name=name: http_ready(addresses[name], name)
        services.append(Service(name, commands[name], ROOT / 'frontend' if name == 'frontend' else ROOT,
                                ready, existing, addresses.get(name, '数据库心跳')))
    return Runtime(ROOT, services, environment=env), preflight, close


def main():
    parser = argparse.ArgumentParser(description='Probe 本地服务启动、状态和停止；不安装依赖、不重置数据。')
    parser.add_argument('command', choices=['start', 'status', 'stop'])
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('当前启动器仅支持 POSIX。')
    runtime = None
    close = lambda: None
    try:
        # stop 使用已有归属记录，不要求数据库仍然可用。
        if args.command == 'stop':
            services = [Service(name, [], ROOT, lambda pid: False, lambda: None) for name in NAMES]
            return 0 if Runtime(ROOT, services).stop() else 1
        runtime, preflight, close = configure()
        if args.command == 'status':
            return 0 if runtime.status() else 1
        preflight(); runtime.start()
        print(f"工作台：{runtime.services['frontend'].address}；数据页：{runtime.services['frontend'].address}/#data")
        print('日志：.local/dev-runtime/。复用的手动服务不会由 stop 关闭。')
        return 0
    except KeyboardInterrupt:
        print('启动被中断，已尝试回收本次创建的服务。', file=sys.stderr)
        return 130
    except RuntimeErrorDetail as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f'配置无法读取（{type(exc).__name__}），请检查 .env；未输出连接凭据。', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'本地服务操作未完成（{type(exc).__name__}），请检查配置及日志。', file=sys.stderr)
        return 1
    finally:
        close()


if __name__ == '__main__':
    raise SystemExit(main())
