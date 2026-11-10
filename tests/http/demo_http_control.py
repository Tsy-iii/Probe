"""平台显式选择的慢 HTTP 服务演示，默认 pytest 不收集。"""
import time
import pytest

pytestmark = pytest.mark.postgres


def test_http_service_waits_for_execution_control(http_service, http_checks):
    # 服务已经真实就绪，保持 8 秒供取消／超时／Worker 恢复验证。
    time.sleep(8)
    response = http_checks.send('GET', '/products/1', label='HTTP 慢执行结束后查询')
    assert response.status_code == 200
