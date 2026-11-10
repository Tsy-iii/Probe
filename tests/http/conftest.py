"""网络套件的服务和请求 fixture。database 仍由上层提供真实 PostgreSQL 隔离。"""
import time
from contextlib import contextmanager

import httpx
import pytest

from probe.http_testing import HTTPTestService
from probe.test_evidence import check, database_snapshot, record_http_evidence, record_transport_evidence


class HTTPChecks:
    def __init__(self, client, service, database, request):
        self.client, self.service, self.database, self.request = client, service, database, request

    def send(self, method, path, *, label, expected_status=None, expected_fields=None, **kwargs):
        # 在真正发 TCP 请求前后采集状态；断言失败时证据也已经保存。
        before = database_snapshot(self.database)
        started = time.perf_counter()
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            elapsed = time.perf_counter() - started
            record_transport_evidence(self.request, exc, label=label, before=before, after=database_snapshot(self.database), checks=[],
                                      timeout_seconds=self.client.timeout.read, elapsed_seconds=elapsed)
            raise
        elapsed = time.perf_counter() - started
        assertions = [check('HTTP 状态码', expected_status, response.status_code)] if expected_status is not None else []
        if expected_fields:
            body = response.json()
            for name, expected in expected_fields.items():
                actual = body
                for field in name.split('.'):
                    actual = actual.get(field) if isinstance(actual, dict) else None
                assertions.append(check(name, expected, actual))
        record_http_evidence(self.request, response, label=label, before=before, after=database_snapshot(self.database), checks=assertions,
                             transport={"mode": "network", "service_id": self.service.id, "base_url": self.service.base_url,
                                        "timeout_seconds": self.client.timeout.read, "elapsed_seconds": elapsed})
        return response


@pytest.fixture
def http_service_factory(database, tmp_path, request):
    @contextmanager
    def factory(*, allow_faults=False):
        service = HTTPTestService(database, tmp_path, request.node.nodeid, allow_faults=allow_faults)
        try:
            service.start()
            yield service
        finally:
            service.stop()
    return factory


@pytest.fixture
def http_service(http_service_factory):
    with http_service_factory() as service:
        yield service


@pytest.fixture
def http_checks(http_service, database, request):
    with http_service.client() as client:
        yield HTTPChecks(client, http_service, database, request)
