# Failure investigations / 故障复盘

Three reproducible, controlled experiments showing what Probe can preserve and verify when a test or execution fails. These are local test scenarios, not production incidents or measured business benefits.

| Case | Symptom | Evidence and conclusion | Guide |
| --- | --- | --- | --- |
| Incorrect order amount | HTTP 201, but the amount assertion fails | Expected 3980 cents, actual 1990; HTTP and PostgreSQL evidence remain after cleanup | [金额计算错误](incorrect-amount.md) |
| Lost response after commit | Platform returns 504 with an unknown result | The order committed: stock 5 → 3, one order; only one creation request | [提交后响应丢失](response-lost-after-commit.md) |
| Worker loss | An active attempt is interrupted | Replacement attempt, stale-owner rejection, process and schema cleanup | [Worker 中断与恢复](worker-loss.md) |

## 本轮验证结果

2026-11-10，测试运行时的仓库 HEAD 为 `4feb103da8ad19395ccc3ad010ee27d8fe58922d`。使用真实 PostgreSQL 17.9 和独立测试空间，运行以下 6 条检查：

| 检查 | 实际结果 |
| --- | --- |
| 故障金额实现 | failed：故意产生 1990 分，违背 3980 分契约 |
| 正确金额实现 | passed |
| commit 后丢弃响应 | passed：验证 504 未确认、已落库、没有重复请求 |
| 自建 Worker 被 SIGKILL 后恢复 | passed |
| 旧尝试结果写回拒绝 | passed |
| 替代 Worker 清理两份 schema | passed |

合计 **1 failed、5 passed、0 errors、0 skipped**，保留 1 条既有 TestClient 弃用提示。这个组合命令的退出码是 1，因为它显式包含故意失败的金额用例；不能将它改写为“全部通过”。默认回归不直接收集 `tests/demo_amount_bug.py`，完整 CI 的既有 194 passed 记录与此受控实验不是同一次运行。

本轮没有修改业务代码，没有修复真实线上事故，也没有增加运行时 AI、UI 自动化或新的任务调度模块。

## 怎样读一份复盘

1. 看症状：区分 HTTP 返回、业务断言和任务执行状态。
2. 看证据：确认来自实际响应、数据库查询，还是测试控制源码。
3. 看定位：依据哪些字段排除问题，把范围缩小到哪段代码。
4. 看恢复与验证：实现已经保证了什么，仍然不能保证什么。

## 复现边界

- 金额故障使用测试进程中的 monkeypatch，并实际写入测试数据库；没有更改常驻订单服务。
- 响应丢失使用 ASGITransport 替身，在真实 commit 后抛出 ReadTimeout；它不是实际 TCP 断网实验。
- SIGKILL 只控制用例自建的 Worker；lease/fencing 的独立检查直接操作测试数据库，不通过公网请求。
- Worker 用例验证最终状态、进程和 schema，但没有采集所有中间状态的 UI 时间线或 HTTP 证据。
- 原始 JUnit 和日志保存在本地忽略的验证目录，公开文档保留可复现命令和脱离私人标识的事实，不发布实际 PID、任务 UUID、凭据或个人目录。

平台最终结果以 pytest 为准。受控故障正确被发现时，业务用例可以 failed，而检查平台正确处理该失败的外层测试可以 passed。
