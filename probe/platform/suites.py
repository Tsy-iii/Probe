"""可信测试套件的白名单；新增用例文件在这里明确接入。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Suite:
    id: str
    name: str
    description: str
    targets: tuple[str, ...]
    requires_postgres: bool = False
    expected_failure: bool = False
    requires_platform_test: bool = False


SUITES = {
    "demo_orders": Suite("demo_orders", "演示订单接口回归", "验证页面使用的订单转发接口：下单、支付、取消、重复请求和异常；两份测试库独立隔离，保留请求及数据库证据。", ("tests/platform/test_demo_orders.py",), True, requires_platform_test=True),
    "http_orders": Suite("http_orders", "真实 HTTP 订单测试", "独立服务、真实 TCP 请求与 PostgreSQL；覆盖业务流程、读取超时和连接失败。", ("tests/http/test_orders_http.py",), True),
    "http_control": Suite("http_control", "HTTP 服务清理演示", "保持独立 HTTP 服务 8 秒，观察任务取消、超时及服务进程清理。", ("tests/http/demo_http_control.py",), True),
    "stock_boundary": Suite("stock_boundary", "库存边界测试", "买完全部库存，再次购买应被拒绝；同时检查实际数据库结果。", ("tests/test_stock_boundary.py",), True),
    "amount_bug_demo": Suite("amount_bug_demo", "金额计算故障对照", "隔离对照两种实现：漏乘数量时失败，正确公式回归通过。", ("tests/demo_amount_bug.py",), True, True),
    "stage1": Suite("stage1", "订单 API 完整测试", "校验订单流程、真实 PostgreSQL 事务、幂等与并发。", ("tests/test_contract.py", "tests/test_orders.py"), True),
    "contract": Suite("contract", "接口契约测试", "校验参数、健康检查、请求 ID 和故障开关。", ("tests/test_contract.py",)),
    "failure_demo": Suite("failure_demo", "固定错误演示", "确定性 503 与预期 201 冲突，观察真实失败证据。", ("examples/pytest_failure_demo.py",), expected_failure=True),
    "slow_demo": Suite("slow_demo", "慢执行演示", "执行等待 8 秒并创建子进程，用于观察超时、取消与清理。", ("examples/pytest_slow_demo.py",)),
    "isolation": Suite("isolation", "订单隔离验证", "在本次尝试的独立 PostgreSQL schema 中运行创建、查询与支付。", ("tests/test_orders.py::test_create_query_and_pay",), True),
}
