# 演示订单接口回归套件

> 当前入口见 [主 README](../README.md)；文末旧验收数字和截图为有日期的历史记录，不代表当前服务状态。`.local/` 与 `test-results/` 仅为运行产物位置，历史原始文件不随仓库发布。

入口：<http://127.0.0.1:5174> →「演示订单接口回归」（`demo_orders`）。它检查数据页下单、模拟支付和取消所使用的平台转发接口，和直接调用订单 API 的套件属于不同验证范围。

## 怎样体验

1. 刷新工作台，选择「演示订单接口回归」。
2. 点击「查看 / 选择」，查看 pytest 实际收集的 21 条用例及参数、源码位置、中文说明。可以全部执行，也可以只选「演示取消与迟到支付」。
3. 提交任务。等待状态结束，打开报告并展开一条用例。
4. 查看请求路径、请求 ID、实际 HTTP 状态、检查的预期／实际值，以及数据库请求前后快照。
5. 展开取消用例：创建两件后库存 3，第一次取消后为 5，第二次取消仍为 5；迟到支付返回 409，事件数保持 0。
6. 在数据浏览页核对演示商品。测试使用独立数据，不改变你新增的商品或手动订单。

业务返回 409 不等于测试失败：库存不足、金额不匹配、已支付取消等用例的断言就是要求正确拒绝，并检查数据不变。套件中的响应丢失场景明确在真实 commit 后丢弃响应；因此预期为“504 未确认，但订单已创建”，而非“504 必然回滚”。

## 验证范围

用例文件为 `tests/platform/test_demo_orders.py`，主要覆盖：

| 场景 | 关键检查 |
| --- | --- |
| 下单、查询、支付和重放 | HTTP、金额、库存、状态、事件去重、请求 ID |
| 取消、重复取消和迟到支付 | 库存只恢复一次，已取消订单不能支付 |
| 错误金额、冲突事件、已支付取消 | 正确错误码及数据库保持正确状态 |
| 数量与金额参数 | 每次只改变一个字段，422 定位对应字段 |
| 不存在、库存不足、已删除商品 | 创建被拒绝，不残留订单或错误扣库存 |
| 大整数金额 | 原始 JSON、字符串摘要和 PostgreSQL 金额精确一致 |
| 演示关闭、数据来源不一致 | 转发前拒绝，不操作错误数据库 |
| 提交后响应丢失 | 保留未知结果，不自动重试或重复创建 |
| 实际 TCP 链路 | 通过本用例拥有的订单服务进程发网络请求，核对业务与进程清理 |

大多数用例通过 TestClient 和 ASGITransport，在进程内连接平台与订单应用，数据库使用真实 PostgreSQL。TCP 用例单独启动隔离订单服务，不依赖手动 8002 服务。ASGI 用例不是网络测试；固定连接故障是显式模拟传输，不能当作线上网络事故证明。

## 两份测试数据如何隔离

每次执行尝试有自己的 UUID。同一尝试对应两份空间：

```text
probe_test
  └─ run_<attempt UUID 的 32 位 hex>
      └─ products / orders / payment_events

probe_platform_test
  └─ platform_case_<同一个 attempt UUID 的 32 位 hex>
      └─ test_runs / run_attempts / workers
```

真正的工作台历史继续保存在平台库 `probe_platform`；用例构建测试平台应用时使用 `probe_platform_test`。

Worker 在启动 pytest 前创建两份空间，并把连接 URL、订单 schema、平台测试 schema 和 attempt ID 显式传给子进程。每条用例建表和准备自己的商品，结束后清理表；Worker 在执行结束后删除两个 schema。

平台测试空间的归属标记先写入 `.local/runs/{run_id}/{attempt_id}/platform-test-schema`，再执行创建 DDL。即使创建或启动失败，新 Worker 仍能找到归属信息并清理。清理时只接受与当前 attempt ID 精确匹配的名称，标记不能指定其他数据库或任意 schema。

演示写入对 `run_` 的放行只用于测试工厂：必须是 `APP_ENV=test`、显式注入的 `probe_test`，且订单 schema、平台 schema 和 attempt ID 全部一致。页面不接受 schema 输入；测试库 public 或不匹配的空间继续拒绝写入。

## 为什么结束后还有报告

`DemoChecks` 在发请求前后查询测试数据库，在用例断言执行前把证据加入 pytest 的 `user_properties`。JUnit 保存这些 properties，平台解析后写入 JSONB 报告，因此表清掉后报告仍保留证据。

证据中的金额期望由请求数量与准备时单价计算，不从实际响应倒填。期望 HTTP 状态由用例明确传入。原始 pytest 断言负责最终用例结果，采集的检查行作为辅助证据，不能覆盖失败结果。

用例目录提供中文说明；真实用例 ID、数量、参数和源码位置来自 pytest 收集。源码或 fixture 改变时重新收集目录，提交仍保存当时的执行范围。

## 本地运行与环境

```bash
.venv/bin/python -m pytest tests/platform/test_demo_orders.py --require-postgres -q
.venv/bin/python -m pytest tests/platform/test_demo_suite.py --require-postgres -q
.venv/bin/python -m pytest --require-postgres -q
```

工作台执行需要配置 `TEST_DATABASE_URL`（`probe_test`）和 `PLATFORM_TEST_DATABASE_URL`（`probe_platform_test`）。运行环境检查中会分别展示两项；缺少平台测试库只阻断这组套件的就绪状态，其他套件按各自依赖判断。修改配置或代码后重启平台 API 与 Worker，沿用现有 8001、5174 端口。

本轮没有新增业务数据库迁移，没有重置演示库。GitHub 仓库与 Actions 在本次增量完成后再接入。

## 核心代码

- `probe/platform/suites.py`：可信套件白名单与依赖要求。
- `probe/platform/runner.py`：两份空间的创建、子进程配置与正常／异常清理。
- `tests/platform/conftest.py`：Worker 空间内逐用例准备与清理平台表。
- `tests/platform/test_demo_orders.py`：接口断言与清理前证据。
- `tests/platform/test_demo_suite.py`：从提交到报告的真实 Worker 验证、取消、受控失败及替代 Worker 恢复。

## 实际验证与下一步

2026-11-10：聚焦接口、环境与 Worker 验证 43 passed；最终完整 PostgreSQL 回归 194 passed、0 failed、0 skipped，保留 1 条既有 Starlette TestClient 弃用提示。前端构建通过。

当前工作台通过真实提交执行了新套件，21/21 通过，报告中的全部用例都保留请求证据；已只读确认两份属于该尝试的 schema 均不存在，用户演示数据前后核对一致。浏览器验证使用真实 API 和 Worker，没有拦截响应或模拟业务写入。

报告地址由工作台的「复制报告链接」生成；读者应运行本次任务，不依赖历史本地任务地址。

完整回归曾发现新增环境检查 ID 未同步响应模型，导致环境检查报错；已修复并重新验证通过。受控断言失败、取消、启动失败和替代 Worker 清理均在独立测试空间验证，不污染工作台的演示数据。

本地基础能力已具备接入基础 CI 的条件。下一步 GitHub Actions 可限定为 PostgreSQL 服务、依赖安装、完整 pytest、前端构建，以及失败时保留 JUnit／日志。GitHub 仓库已创建，CI 配置见 [GitHub Actions](ci.md)；首次云端执行和 Linux 验证结果待实际运行确认。
