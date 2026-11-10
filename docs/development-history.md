# 开发历史与验证里程碑

本文记录本地开发里程碑，帮助理解技术选择的演进。它不提供当前运行状态，也不要求读者拥有任何历史任务。当前启动和验证以 [README](../README.md)、[中文导航](README.zh-CN.md) 和 [验证说明](verification.md) 为准。

| 里程碑 | 交付内容 | 历史验证记录 |
| --- | --- | --- |
| 订单 API | PostgreSQL 库存事务、支付幂等、取消、确定性故障和接口测试 | 2026-11-05 本地记录：20 passed；真实 PostgreSQL，无 SQLite 替代 |
| 可视化执行 | 持久任务、pytest 后台执行、状态、JUnit 与日志 | [第二阶段历史验证](stage2-verification.md) |
| 执行控制 | 提交去重、超时、取消、lease、fencing、独立尝试和 Worker 恢复 | [第三阶段历史验证](stage3-verification.md) |
| 用例与诊断 | 收集/选择用例、保存范围、数据库证据、历史筛选和关联重跑 | [用例选择](case-selection.md)、[历史与报告](history-and-reports.md) |
| HTTP 链路 | 每例独立 TCP 服务、网络异常证据、进程与 schema 清理 | [HTTP 测试](http-testing.md) |
| 演示业务 | 数据浏览、商品管理、页面下单/支付/取消 | [商品管理](product-management.md)、[演示接口](demo-order-operations.md) |
| 演示回归套件 | Worker 管理订单与平台两份测试数据，保留失败证据 | 2026-11-10 本地记录：完整回归 194 passed；[套件说明](demo-suite.md) |

历史截图展示当时界面与实际本地记录，不是当前版本的功能入口、生产指标或预填成功率。部分本地浏览器验收使用模拟回复；真实业务断言由 PostgreSQL/API 测试验证，TCP 范围由专门的网络用例验证。

开发阶段文档保留用于解释迭代背景。历史测试数字只对应当时版本和环境，不能替代当前回归。阶段名称不会继续作为主 README 的内容结构。

GitHub Actions 和正式提交的 Playwright 测试体系尚未接入。项目使用 Coding Agent 辅助实现、重构、测试和文档，不以“独立从零设计实现”描述开发经历。
