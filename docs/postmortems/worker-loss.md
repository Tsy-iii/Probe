# Worker 中断：恢复尝试、拒绝旧结果、清理资源

这是自建测试 Worker 的受控中断实验。只停止用例启动的进程，不对正在使用的本地平台 Worker 发送信号，也不声称验证了生产分布式系统的全部故障。

## 症状与目标

任务正在执行时，负责它的 Worker 被强制结束。系统需要避免任务永远卡住，并处理：

- 旧执行进程和测试 schema 的清理。
- 新 Worker 能否在 lease 过期后接管。
- 旧所有者或旧 attempt 能否写回并覆盖新结果。
- 恢复重试是否有次数上限，报告是否能区分两次尝试。

## 复现与本轮结果

```bash
.venv/bin/python -m pytest tests/platform/test_reliability.py::test_sigkill_worker_is_recovered_with_new_attempt --require-postgres -v
.venv/bin/python -m pytest tests/platform/test_reliability.py::test_old_attempt_cannot_write_after_lease_recovery --require-postgres -v
.venv/bin/python -m pytest tests/platform/test_demo_suite.py::test_replacement_worker_recovers_both_schema_ownership --require-postgres -v
```

本轮三条检查通过，范围不同：

| 检查 | 实际验证的断言 |
| --- | --- |
| SIGKILL 自建 Worker | 同一任务 2 次尝试：`interrupted`、`passed`；第二次 20 条通过，最终报告属于第二次；旧进程停止、两个订单测试 schema 均清理 |
| 旧写回拒绝 | 新所有者接管或开始新尝试后，旧 `finish()` 返回 False，不能把任务改成通过或覆盖报告 |
| 双 schema 恢复 | 人为到期 lease 后，替代 Worker 清理订单和平台两份空间；该实验 `max_attempts=1`，最终 error、尝试 interrupted，没有无限重试 |

SIGKILL 用例真实启动进程。后两条通过测试数据库中的确定性归属/lease 构造验证 repository 和清理路径，不应描述成三次真实进程崩溃。

## 恢复机制

设计路径是：运行中的旧尝试 → 取得恢复职责 → 清理 → 有剩余次数时排队新尝试 → 新尝试执行。它是源码定义的状态机；本轮测试断言终态和尝试记录，没有录制每一个 UI 中间状态。

[Repository](../../probe/platform/repository.py) 用 PostgreSQL 行锁领取任务或恢复职责。有效 lease 表示当前所有权；过期后旧 Worker 不能继续续租。

结果提交要在事务内同时匹配 Worker、当前 attempt、状态和有效 lease。直接 fencing 检查调用旧身份提交“passed”，数据库拒绝接受；这里只是内部归属约束，不是用户登录权限。

[Worker](../../probe/platform/runner.py) 接管后先清理旧尝试再决定是否重排队。订单测试空间在 `probe_test`；演示接口回归额外使用 `probe_platform_test`。平台空间的归属标记与 attempt 绑定，让替代 Worker 可以找到清理职责。

## 什么证据可用

- 任务记录区分每次 attempt、停止原因、清理状态和最终报告来源。
- 验证程序检查旧进程不再存活，并实际查询 PostgreSQL 的 namespace 确认空间不存在。
- SIGKILL/直接 repository 用例的 JUnit 表明断言通过；本轮没有给它们附加结构化 HTTP 交换或完整 UI 时间线。
- `cleanup_done` 覆盖活动测试进程和 schema；日志、报告和工作目录仍保留，不表示所有文件都已删除。

## 边界与面试展开点

Worker 丢失可以触发新尝试。它与断言失败后盲目重跑不同：触发原因、次数和每次结果都保留，超时／取消／断言失败不被重试成绿色。

这不保证外部副作用只发生一次。新尝试可能重新执行测试；当前可信套件使用自己的隔离数据，使恢复可验证。恢复还依赖 PostgreSQL 可用，不覆盖所有网络分区、主机崩溃、恶意测试或内核故障。

面试展开点：为什么新 Worker 清理前就要改变归属？为什么检查 Worker ID 之外还要检查 attempt ID 和 lease？为什么保留旧尝试的 interrupted，而不把它删除？
