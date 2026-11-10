# 订单 API 的契约测试：检查输入校验、健康响应、请求 ID 和故障开关。
# 这些路径无需连接数据库，不验证库存事务；数据持久化测试见 test_orders.py。
# pytest 自动提供的 caplog 用来捕获日志，parametrize 用来逐项替换一个参数。

import logging
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from probe.config import Settings
from probe.main import create_app


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True])
def test_quantity_is_a_positive_integer(offline_client, quantity):
    # 只改变 quantity；商品 ID 保持 1，分别检查零、负数、小数和布尔值。
    response = offline_client.post("/orders", json={"product_id": 1, "quantity": quantity})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    # 除了 422，还要求错误定位到 quantity，排除由其他字段引起的校验失败。
    assert response.json()["details"][0]["loc"] == ["body", "quantity"]
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


def test_health_request_id_and_fault_disabled(offline_client, caplog):
    # 默认关闭故障注入：即使带 error 请求头，也应正常响应。
    # 同时检查自定义请求 ID 能从响应头关联到日志。
    with caplog.at_level(logging.INFO, logger="probe.requests"):
        response = offline_client.get("/health", headers={"X-Request-ID": "learn-001", "X-Probe-Fault": "error"})
        # /health 本身豁免故障；再检查可注入故障的 /openapi.json，验证全局开关。
        normal = offline_client.get("/openapi.json", headers={"X-Probe-Fault": "error"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "learn-001"
    assert normal.status_code == 200
    assert "request_id=learn-001" in caplog.text


def test_deterministic_faults_require_explicit_enable():
    # 显式开启测试故障，固定延迟为 80ms；不使用随机异常。
    settings = Settings(
        database_url="postgresql+psycopg://probe:unused@127.0.0.1:1/probe",
        app_env="test", fault_injection_enabled=True, fault_delay_ms=80,
    )
    with TestClient(create_app(settings)) as client:
        # 两次独立调用各自必须得到 503，用来确认可复现性，不是重试到成功。
        for _ in range(2):
            response = client.post("/orders", json={"product_id": 1, "quantity": 1}, headers={"X-Probe-Fault": "error"})
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "injected_error"
        started = time.perf_counter()
        # perf_counter 测量真实耗时。只断言延迟下限，机器繁忙时可能更慢。
        delayed = client.get("/openapi.json", headers={"X-Probe-Fault": "delay"})
        assert delayed.status_code == 200
        assert time.perf_counter() - started >= 0.08
        unknown = client.get("/openapi.json", headers={"X-Probe-Fault": "random"})
        # 未定义的故障名称应明确拒绝，不能变成随机行为。
        assert unknown.status_code == 400
    with pytest.raises(ValidationError, match="only allowed"):
        # 这里检查配置校验：production 环境禁止启用开发故障。
        Settings(app_env="production", fault_injection_enabled=True)
