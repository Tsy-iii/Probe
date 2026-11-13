# Probe 项目问答

这份问答面向第一次阅读仓库的人。实现入口和已有测试可以查阅；测试文件存在不表示你已运行通过，带日期的验证结果见[验证说明](verification.md)。

## 1. Probe 到底在测试什么？

订单 API 提供库存、金额、支付和取消的业务场景；测试代码发请求并执行断言。平台负责接受任务、由 Worker 执行 pytest、保存状态和失败证据。前端是选择、控制和查看入口。

例如，选择订单套件后，平台会运行仓库用例来检查“下单扣库存，支付后状态为 paid”。它不把页面选择自动变成新断言。

入口：[套件注册](../probe/platform/suites.py)、[执行器](../probe/platform/runner.py)、[订单测试](../tests/test_orders.py)。

## 2. 断言写在哪里，可以选择自己的用例吗？

断言写在 `tests/` 等仓库用例中。平台通过 pytest 收集实际 case ID，包括参数化实例；你可以选择全套或子集。新增用例需纳入可信套件白名单，接口不接受任意上传脚本或命令。

提交时保存精确用例范围；排队后新增的 case 不会自动混入。但当前没有冻结同名源码和依赖，保存选择不等于完整版本快照。

入口：[收集与选择说明](case-selection.md)、[选择验证](../tests/platform/test_case_selection.py)。

## 3. 测试数据在哪里，为什么不影响页面数据？

| 数据库 | 用途 |
| --- | --- |
| `probe` | 手动演示商品、订单和支付事件 |
| `probe_platform` | 工作台真实任务、attempt 和报告 |
| `probe_test` | 订单自动测试的临时数据 |
| `probe_platform_test` | 平台自身测试的临时记录 |

直接运行 pytest 时，数据库用例使用随机 `case_…` schema。平台执行时，Worker 为 attempt 创建 `run_…` schema；同一 attempt 内用例顺序执行，fixture 为每条用例重新建表、准备前置数据、清理表，最终 Worker 删除空间。平台测试另用 `platform_case_…`。

并发用例中的多个请求有意共享同一商品，才能验证竞争。schema、目录和进程隔离用于可信测试，不是权限或安全沙箱，也不提供独立机器资源。

入口：[订单 fixture](../tests/conftest.py)、[平台 fixture](../tests/platform/conftest.py)、[隔离与恢复设计](architecture.md#process-directory-and-database-isolation)。

## 4. 在页面改价、改库存，会让固定断言失败吗？

页面编辑演示库；隔离测试由 fixture 准备自己的商品。因此只改演示商品不会直接改掉测试里的价格和库存。演示改价不重新计算旧订单金额，新订单使用新价格。

如果改变的是业务契约，比如从“创建时扣库存”改成“支付时扣库存”，实现和断言都需要重新评审。隔离解决前置数据污染，不替代测试维护；不能仅为变绿把预期值改成错误实际值。

入口：[商品编辑](../probe/platform/demo_products.py)、[旧订单金额与新价格验证](../tests/platform/test_demo_products.py)。

## 5. TestClient 与真实 HTTP 套件有什么不同？

TestClient 在测试进程内调用注入隔离数据库的应用，不访问手动演示 API。真实 HTTP 套件启动独立订单服务，绑定本机动态 TCP 端口，并连接本次测试 schema；结束时停止服务。

两者均可检查数据库结果。真实 TCP 额外覆盖连接与读取异常、监听进程及服务清理，但不证明公网或生产网络行为。

入口：[HTTP 测试说明](http-testing.md)、[服务生命周期实现](../probe/http_testing.py)、[网络接口用例](../tests/http/test_orders_http.py)。

## 6. HTTP 201 正常，为什么测试仍失败？CI 通过又是什么意思？

HTTP 状态符合预期，不等于业务字段正确。金额故障对照中，购买 2 件、单价 1990 分应得 3980 分，错误实现只返回 1990 分；201 检查可通过，金额断言仍失败。原始 pytest 结果是事实依据，页面检查与作者 hint 只是阅读和排查线索。

外层平台回归可以通过，因为它断言内层错误用例确实失败，并且证据被保留。这不表示内层业务已修复，也不是把失败重试成成功。

任务取消、执行超时、连接异常和断言失败亦有不同语义。客户端读取超时不代表服务端没有提交，见[提交后丢响应复盘](postmortems/response-lost-after-commit.md)。

入口：[金额复盘](postmortems/incorrect-amount.md)、[报告解析](../probe/platform/reports.py)、[失败证据验证](../tests/platform/test_case_evidence.py)。

## 7. run、attempt、重放、重跑和恢复分别是什么？

- `run`：一次逻辑测试任务，保存参数、选择范围和最终报告。
- `attempt`：一次实际执行。Worker 失联后的恢复可能产生新 attempt，旧历史保留。
- `replay`：同提交键、同参数返回原 run；冲突内容拒绝。
- `rerun`：创建关联旧任务的新 run，保留原结果并校验范围。
- `recovery`：接管旧 run，先清理旧资源，再按有限次数决定是否重新执行。

结果写回检查 Worker、active attempt、状态和有效 lease，避免旧执行覆盖新职责。它保护结果归属，不撤销外部副作用；不宣称 exactly-once。超时、取消和业务断言失败不自动重试成通过。

入口：[执行设计](architecture.md#leases-fencing-and-recovery)、[可靠性用例](../tests/platform/test_reliability.py)、[关联重跑用例](../tests/platform/test_rerun.py)。

## 8. 为什么没有在线站点，怎样判断项目验证了什么？

当前交付是可本地运行的项目与源码仓库，没有托管演示网站。网页之外还需 Python API、PostgreSQL 和能启动子进程的 Worker；静态网页托管不能原样运行当前完整执行链。截图和复盘用于免安装阅读，实际执行按 README 启动。

判断验证结论时看代码版本、运行日期、环境和断言范围。用例数不是覆盖率；前端构建通过不是浏览器交互回归；本地多 Worker 不代表生产分布式部署。当前没有正式提交的 Playwright 套件、认证、多租户或安全沙箱，运行时不调用 AI 服务。

入口：[验证范围](verification.md)、[CI 分批记录](ci.md)、[已知边界](../README.zh-CN.md#已知边界)。
