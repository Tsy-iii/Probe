"""Validate the real local launcher, Vite proxy, Worker, report, and owned cleanup.

Default: a fresh project-local PostgreSQL instance. --ci: the disposable Actions
PostgreSQL service, using explicit job environment values. Neither mode reads the
checkout's .env or launches application processes in the original checkout.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from uuid import uuid4
from xml.etree import ElementTree

import httpx
import psutil
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
DATABASES = {
    'DATABASE_URL': 'probe',
    'TEST_DATABASE_URL': 'probe_test',
    'PROBE_PLATFORM_DATABASE_URL': 'probe_platform',
    'PLATFORM_TEST_DATABASE_URL': 'probe_platform_test',
}
SERVICES = {'order', 'platform', 'worker', 'frontend'}


class SmokeFailure(Exception):
    """An observed check failure; never retry it into a pass."""


def require(condition, message):
    if not condition:
        raise SmokeFailure(message)


def reserve_ports(count):
    # Keep reservations open together so a single run cannot select duplicates.
    sockets = []
    try:
        for _ in range(count):
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0))
            sockets.append(listener)
        return [listener.getsockname()[1] for listener in sockets]
    finally:
        for listener in sockets:
            listener.close()


def fresh_environment(config):
    env = {key: value for key, value in os.environ.items()
           if key not in {*DATABASES, 'APP_ENV', 'PYTHONPATH'}
           and not key.startswith(('PROBE_', 'FAULT_', 'PYTEST_', 'VITE_', 'PG'))}
    env.update(config)
    return env


def tagged_processes(workspace):
    matches = []
    for process in psutil.process_iter():
        try:
            if process.status() != psutil.STATUS_ZOMBIE and process.environ().get('PROBE_DEV_ROOT') == str(workspace):
                matches.append(process.pid)
        except psutil.Error:
            continue
    return matches


def port_open(port):
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(('127.0.0.1', port)) == 0


def connect(url):
    parsed = make_url(url)
    return psycopg.connect(parsed.set(drivername='postgresql').render_as_string(hide_password=False), connect_timeout=3)


class Smoke:
    def __init__(self, args):
        self.args = args
        self.id = uuid4().hex
        self.workspace = ROOT / '.local' / ('smoke-' + self.id[:8])
        self.output = Path(args.output_dir).resolve() / self.id
        self.summary = {'version': 1, 'success': False, 'mode': 'ci' if args.ci else 'local',
                        'steps': [], 'cleanup': {}, 'run': None}
        self.config = {}
        self.pg_binaries = None
        self.pg_data = self.workspace / '.local' / 'postgres'
        self.environment = fresh_environment({})
        self.app_ports = []
        self.can_stop_apps = False

    def command(self, label, command, expected=0, timeout=90):
        with (self.output / (label + '.log')).open('wb') as log:
            result = subprocess.run(command, cwd=self.workspace, env=self.environment,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        self.summary['steps'].append({'name': label, 'exit_code': result.returncode})
        require(result.returncode == expected, f'{label}: unexpected exit code {result.returncode}; see saved log')

    @property
    def python(self):
        return str(self.workspace / '.venv' / 'bin' / 'python')

    def launcher(self, label, action, expected=0):
        self.command(label, [str(self.workspace / 'scripts' / 'probe'), action], expected=expected)

    def prepare(self):
        require(os.name == 'posix', 'Runtime smoke requires POSIX')
        require((ROOT / '.venv/bin/python').is_file(), 'Prepare .venv using requirements.lock.txt')
        require(shutil.which('node') is not None and (ROOT / 'frontend/node_modules/vite/bin/vite.js').is_file(),
                'Prepare Node.js and frontend dependencies with npm ci')
        if self.args.ci:
            require(os.environ.get('GITHUB_ACTIONS') == 'true', '--ci requires a disposable GitHub Actions runner')
            self.config = {key: os.environ.get(key, '') for key in DATABASES}
            endpoints = []
            for key, name in DATABASES.items():
                url = make_url(self.config[key])
                require(url.drivername == 'postgresql+psycopg' and url.database == name and url.host == '127.0.0.1',
                        f'{key}: expected the explicit loopback CI database {name}')
                endpoints.append((url.host, url.port, url.username))
            require(len(set(endpoints)) == 1, 'All CI databases must use the same disposable PostgreSQL service')
        else:
            try:
                from pgembed._commands import POSTGRES_BIN_PATH
            except ImportError:
                raise SmokeFailure('Local smoke needs requirements-local-db.txt; no database validation performed')
            self.pg_binaries = Path(POSTGRES_BIN_PATH)
            require((self.pg_binaries / 'pg_ctl').is_file(), 'Optional PostgreSQL binaries are unavailable')
        ports = [8002, 8001, 5174] if self.args.ci else reserve_ports(4)
        require(not any(port_open(port) for port in ports[:3]), 'Selected application ports are occupied')
        order_port, platform_port, frontend_port = ports[:3]
        self.app_ports = ports[:3]
        if not self.args.ci:
            self.pg_port = ports[3]
            self.config = {key: f'postgresql+psycopg://probe@127.0.0.1:{self.pg_port}/{name}'
                           for key, name in DATABASES.items()}
        self.config.update(APP_ENV='development', FAULT_INJECTION_ENABLED='false',
                           PROBE_PLATFORM_ORDER_API_BASE_URL=f'http://127.0.0.1:{order_port}')
        if not self.args.ci:
            self.config.update(PROBE_DEV_PLATFORM_PORT=str(platform_port), PROBE_DEV_FRONTEND_PORT=str(frontend_port))
        self.environment = fresh_environment(self.config)
        self.output.mkdir(parents=True, exist_ok=False)
        self.workspace.mkdir(parents=True, exist_ok=False)
        # Copy implementation files unchanged. Dependency installation is shared;
        # application cwd, .env, state, logs, artifacts, and databases are separate.
        for name in ('probe', 'scripts', 'tests', 'migrations', 'examples'):
            shutil.copytree(ROOT / name, self.workspace / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.log', '.pytest_cache'))
        for name in ('alembic.ini', 'pytest.ini'):
            shutil.copy2(ROOT / name, self.workspace / name)
        shutil.copytree(ROOT / 'frontend', self.workspace / 'frontend',
                        ignore=shutil.ignore_patterns('node_modules', 'dist', '.local', '.env', '.env.*', '*.log'))
        (self.workspace / '.venv').symlink_to(ROOT / '.venv', target_is_directory=True)
        (self.workspace / 'frontend/node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
        path = self.workspace / '.env'
        path.write_text('\n'.join(f'{key}={value}' for key, value in self.config.items()) + '\n')
        path.chmod(0o600)
        self.can_stop_apps = True

    def start_postgres(self):
        if self.args.ci:
            # Refuse to apply setup to a nonempty platform history, even in CI.
            with connect(self.config['PROBE_PLATFORM_DATABASE_URL']) as connection:
                exists = connection.execute("SELECT to_regclass('public.test_runs')").fetchone()[0]
                if exists:
                    require(connection.execute('SELECT count(*) FROM test_runs').fetchone()[0] == 0,
                            'CI smoke requires empty disposable platform history')
            return
        self.pg_data.parent.mkdir(parents=True, exist_ok=True)
        self.command('postgres-init', [str(self.pg_binaries / 'initdb'), '-D', str(self.pg_data), '-U', 'probe',
                                      '--auth=trust', '--encoding=UTF8', '--locale=C'])
        # No Unix socket: the smoke workspace can exceed macOS socket path limits.
        self.command('postgres-start', [str(self.pg_binaries / 'pg_ctl'), '-D', str(self.pg_data),
                     '-l', str(self.output / 'postgres.log'), '-o', f"-h 127.0.0.1 -p {self.pg_port} -k ''", '-w', 'start'])
        url = make_url(self.config['DATABASE_URL']).set(database='postgres')
        with connect(url.render_as_string(hide_password=False)) as connection:
            connection.autocommit = True
            for name in DATABASES.values():
                connection.execute(sql.SQL('CREATE DATABASE {} OWNER probe').format(sql.Identifier(name)))

    def check_run(self, client):
        response = client.get('/api/suites')
        require(response.status_code == 200 and any(suite['id'] == 'contract' for suite in response.json()),
                'Frontend proxy did not return the real suite catalog')
        response = client.post('/api/runs', json={'suite_id': 'contract', 'timeout_seconds': 60},
                               headers={'Idempotency-Key': 'runtime-smoke-' + self.id})
        require(response.status_code == 202 and response.json()['replayed'] is False, 'Expected a new accepted job')
        run_id = response.json()['id']
        self.summary['run'] = {'id': run_id, 'status': response.json()['status']}
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            response = client.get(f'/api/runs/{run_id}')
            require(response.status_code == 200, 'Job query failed')
            detail = response.json()
            if detail['status'] in {'passed', 'failed', 'error', 'timed_out', 'cancelled'}:
                break
            time.sleep(0.3)  # Observe the same job; never submit or retry it again.
        else:
            raise SmokeFailure('Job did not finish within the observation deadline')
        (self.output / 'run.json').write_text(json.dumps(detail, indent=2))
        self.summary['run'] = {key: detail[key] for key in ('id', 'status', 'total_count', 'passed_count',
                              'failed_count', 'error_count', 'skipped_count')}
        require(detail['status'] == 'passed' and detail['exit_code'] == 0, 'Contract job did not pass')
        counts = tuple(detail[key] for key in ('total_count', 'passed_count', 'failed_count', 'error_count', 'skipped_count'))
        require(counts == (6, 6, 0, 0, 0), 'Contract result counts differ from the six expected cases')
        require(len(detail['report']['cases']) == 6 and all(case['outcome'] == 'passed' for case in detail['report']['cases']),
                'Persisted report does not contain six passing cases')
        require(len(detail['attempts']) == 1 and detail['attempts'][0]['cleanup_done'], 'Expected one cleaned execution attempt')
        response = client.get(f'/api/runs/{run_id}/junit')
        require(response.status_code == 200, 'JUnit download failed')
        (self.output / 'junit.xml').write_bytes(response.content)
        cases = list(ElementTree.fromstring(response.content).iter('testcase'))
        require(len(cases) == 6 and all(all(case.find(tag) is None for tag in ('failure', 'error', 'skipped')) for case in cases),
                'Downloaded JUnit differs from the saved result')
        response = client.get(f'/api/runs/{run_id}/log')
        require(response.status_code == 200 and '6 passed' in response.json()['content'] and not response.json()['truncated'],
                'Expected the actual pytest output')
        (self.output / 'task.log').write_text(response.json()['content'])
        with connect(self.config['PROBE_PLATFORM_DATABASE_URL']) as connection:
            connection.execute('SET TRANSACTION READ ONLY')
            saved = connection.execute('SELECT status, total_count, passed_count, failed_count, error_count, skipped_count '
                                       'FROM test_runs WHERE id=%s', (run_id,)).fetchone()
            require(saved == ('passed', 6, 6, 0, 0, 0), 'Database result differs from the HTTP response')
            # Terminal runs release their current owner; the attempt retains it.
            worker = connection.execute('SELECT process_id FROM workers WHERE id=%s',
                                        (detail['attempts'][0]['worker_id'],)).fetchone()
            state = json.loads((self.workspace / '.local/dev-runtime/state.json').read_text())
            require(worker and worker[0] == state['processes']['worker']['pid'], 'Job was not executed by the launched Worker')
        self.summary['steps'].append({'name': 'proxied-contract-job', 'status': 'passed'})

    def verify_stopped(self):
        state = json.loads((self.workspace / '.local/dev-runtime/state.json').read_text())
        require(state['processes'] == {}, 'Launcher retained owned service records')
        require(not tagged_processes(self.workspace), 'Owned application processes remain')
        require(not any(port_open(port) for port in self.app_ports), 'An application port still responds after stop')

    def execute(self):
        self.prepare()
        self.start_postgres()
        for label, module, action in [('order-init', 'probe.manage', 'init-db'), ('seed', 'probe.manage', 'seed'),
                                     ('platform-init', 'probe.platform.manage', 'init-db')]:
            self.command(label, [self.python, '-m', module, action])
        self.launcher('before-start-status', 'status', expected=1)
        self.launcher('start', 'start')
        state = json.loads((self.workspace / '.local/dev-runtime/state.json').read_text())
        require(set(state['processes']) == SERVICES, 'All four components must be owned by this smoke run')
        self.launcher('second-start', 'start')
        require(json.loads((self.workspace / '.local/dev-runtime/state.json').read_text()) == state,
                'Repeated start changed the service processes')
        self.launcher('running-status', 'status')
        if self.args.fail_after_start:
            raise SmokeFailure('Explicit failure after startup, used to validate cleanup')
        base = f'http://127.0.0.1:{self.app_ports[2]}'
        with httpx.Client(base_url=base, trust_env=False, timeout=10, follow_redirects=False) as client:
            require(client.get('/').status_code == 200, 'Workbench HTML is unavailable')
            self.check_run(client)
        self.launcher('stop', 'stop')
        self.verify_stopped()
        self.launcher('after-stop-status', 'status', expected=1)

    def cleanup(self):
        errors = []
        if self.can_stop_apps:
            try:
                self.launcher('final-stop', 'stop')
                # Startup can fail before state exists: still require no owned process.
                self.verify_stopped()
                self.summary['cleanup']['applications_stopped'] = True
            except Exception as exc:
                self.summary['cleanup']['applications_stopped'] = False
                errors.append(f'Application cleanup: {type(exc).__name__}')
            for name in SERVICES:
                path = self.workspace / '.local/dev-runtime' / (name + '.log')
                if path.exists():
                    try:
                        shutil.copy2(path, self.output / (name + '.log'))
                    except OSError:
                        errors.append(f'Cannot preserve {name} log')
        if self.pg_binaries and (self.pg_data / 'PG_VERSION').exists():
            try:
                status = subprocess.run([str(self.pg_binaries / 'pg_ctl'), '-D', str(self.pg_data), 'status'],
                                        capture_output=True, timeout=5)
                require(status.returncode in (0, 3), 'Cannot confirm owned PostgreSQL status')
                if status.returncode == 0:
                    self.command('postgres-stop', [str(self.pg_binaries / 'pg_ctl'), '-D', str(self.pg_data),
                                                  '-m', 'fast', '-w', 'stop'])
                status = subprocess.run([str(self.pg_binaries / 'pg_ctl'), '-D', str(self.pg_data), 'status'],
                                        capture_output=True, timeout=5)
                require(status.returncode == 3, 'Owned PostgreSQL still runs after stop')
                self.summary['cleanup']['postgres_stopped'] = True
            except Exception as exc:
                self.summary['cleanup']['postgres_stopped'] = False
                errors.append(f'PostgreSQL cleanup: {type(exc).__name__}')
        elif self.args.ci:
            self.summary['cleanup']['postgres'] = 'Disposable service remains owned by GitHub Actions'
        if errors:
            self.summary['cleanup']['errors'] = errors
        return not errors

    def run(self):
        passed = False
        try:
            self.execute()
            passed = True
        except (Exception, KeyboardInterrupt) as exc:
            self.summary['failure'] = str(exc) if isinstance(exc, SmokeFailure) else type(exc).__name__
        finally:
            cleanup_ok = self.cleanup()
            self.summary['success'] = passed and cleanup_ok
            if self.output.exists():
                (self.output / 'summary.json').write_text(json.dumps(self.summary, indent=2))
        print(json.dumps(self.summary, indent=2))
        if self.output.exists():
            print('Artifacts:', self.output.relative_to(ROOT) if self.output.is_relative_to(ROOT) else self.output)
        return 0 if self.summary['success'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ci', action='store_true', help='Use only the disposable Actions PostgreSQL service')
    parser.add_argument('--output-dir', default=str(ROOT / 'test-results/runtime-smoke'))
    parser.add_argument('--fail-after-start', action='store_true', help='Explicitly fail after readiness to validate cleanup')
    def interrupted(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise SmokeFailure('Smoke interrupted; cleaning owned resources')
    signal.signal(signal.SIGTERM, interrupted)
    return Smoke(parser.parse_args()).run()


if __name__ == '__main__':
    raise SystemExit(main())
