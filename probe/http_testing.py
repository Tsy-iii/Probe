"""HTTP 测试服务生命周期与地址清单；POSIX 的继承 socket 避免端口争抢。"""
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import psutil
from sqlalchemy.engine import make_url

from probe.platform.config import ROOT


def now():
    return datetime.now(timezone.utc).isoformat()


def read_services(path):
    path = Path(path)
    if not path.exists():
        return []
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("HTTP service manifest exceeds 1 MiB")
    document = json.loads(path.read_text())
    if document.get("version") != 1 or not isinstance(document.get("services"), list):
        raise ValueError("Invalid HTTP service manifest")
    if not all(isinstance(record, dict) and isinstance(record.get("id"), str) for record in document["services"]):
        raise ValueError("Invalid HTTP service record")
    return document["services"]


def write_services(path, services):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps({"version": 1, "services": services}, ensure_ascii=False))
    temp.replace(path)


def finalize_services(path):
    # Worker 已验证本次尝试的进程全部停止，再更新未完成的服务记录。
    try:
        services = read_services(path)
    except (OSError, ValueError):
        return  # 元数据损坏不能阻止真实进程／schema 清理；API 会如实显示无法读取。
    changed = False
    for record in services:
        if record.get("status") in {"starting", "ready", "error"}:
            record.update(status="stopped", stopped_at=now(), cleanup_verified=True, stop_reason="attempt_cleanup")
            changed = True
    if changed:
        try:
            write_services(path, services)
        except OSError as exc:
            # 辅助清单无法写回也必须继续清理真实进程和数据库 schema。
            logging.getLogger("probe.http_testing").warning("HTTP service metadata update failed: %s", type(exc).__name__)


def service_processes(service_id):
    processes = []
    for process in psutil.process_iter():
        try:
            if process.status() != psutil.STATUS_ZOMBIE and process.environ().get("PROBE_HTTP_SERVICE_ID") == str(service_id):
                processes.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return processes


class HTTPTestService:
    def __init__(self, database, directory, case_id, *, allow_faults=False, delay_ms=600):
        if os.name != "posix":
            raise RuntimeError("HTTP test service requires POSIX socket inheritance")
        if make_url(database.engine.url).database != "probe_test":
            raise ValueError("Refusing to start HTTP tests against a non-test database")
        if not re.fullmatch(r"(?:case|run)_[a-f0-9]{32}", database.schema or ""):
            raise ValueError("HTTP tests require an isolated case/run schema")
        self.database, self.case_id = database, case_id
        self.id = str(uuid4())
        self.manifest = Path(os.environ.get("PROBE_HTTP_SERVICE_MANIFEST") or Path(directory) / "http-services.json")
        self.directory = self.manifest.parent / "http-services" / self.id
        self.allow_faults, self.delay_ms = allow_faults, delay_ms
        self.process = None
        self.output = None
        self.record = None
        self.base_url = None

    def _save(self, **values):
        self.record.update(values)
        records = read_services(self.manifest)
        records = [self.record if item["id"] == self.id else item for item in records]
        if not any(item["id"] == self.id for item in records):
            records.append(self.record)
        write_services(self.manifest, records)

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=False)
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        self.base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        self.record = {"id": self.id, "case_id": self.case_id, "base_url": self.base_url, "process_id": None,
                       "isolation_schema": self.database.schema, "status": "starting", "started_at": now(), "ready_at": None,
                       "stopped_at": None, "exit_code": None, "cleanup_verified": False, "stop_reason": None}
        self._save()
        environment = os.environ.copy()
        environment.update(PYTHONPATH=str(ROOT), PYTHONUNBUFFERED="1", PROBE_HTTP_SERVICE_ID=self.id,
                           PROBE_HTTP_TEST_DATABASE_URL=self.database.engine.url.render_as_string(hide_password=False))
        command = [sys.executable, "-m", "probe.http_service", "--socket-fd", str(listener.fileno()), "--parent-pid", str(os.getpid()),
                   "--service-id", self.id, "--schema", self.database.schema, "--delay-ms", str(self.delay_ms)]
        if self.allow_faults:
            command.append("--allow-faults")
        self.output = (self.directory / "server.log").open("wb")
        try:
            self.process = subprocess.Popen(command, cwd=ROOT, env=environment, pass_fds=(listener.fileno(),), stdout=self.output, stderr=subprocess.STDOUT)
            self._save(process_id=self.process.pid)
        except BaseException:
            self.stop()
            raise
        finally:
            # 子进程已经继承文件描述符，父进程必须关闭自己的副本，保证退出后释放监听。
            listener.close()
        deadline = time.monotonic() + 8
        try:
            with httpx.Client(timeout=0.3, trust_env=False, transport=httpx.HTTPTransport(retries=0)) as client:
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        raise RuntimeError("HTTP test service exited during startup; inspect server.log")
                    try:
                        response = client.get(self.base_url + "/health/ready")
                        if response.status_code == 200 and response.json().get("status") == "ready":
                            self._save(status="ready", ready_at=now())
                            return self
                    except httpx.RequestError:
                        pass
                    # 只等待服务启动；业务测试请求从不通过重试掩盖失败。
                    time.sleep(0.05)
            raise RuntimeError("HTTP test service did not become ready")
        except BaseException:
            self.stop()
            raise

    def stop(self):
        if self.record is not None and self.record.get("status") == "stopped" and self.record.get("cleanup_verified"):
            return
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=3)
        if self.output is not None:
            self.output.close()
        if self.record is not None:
            if service_processes(self.id):
                raise RuntimeError("HTTP test service did not stop")
            self._save(status="stopped", stopped_at=now(), exit_code=self.process.returncode if self.process else None,
                       cleanup_verified=True, stop_reason="fixture_cleanup")

    def client(self, timeout=2):
        return httpx.Client(base_url=self.base_url, timeout=timeout, trust_env=False, transport=httpx.HTTPTransport(retries=0))
