# 测试与验证

本文区分当前可复现的检查、已有本地验证记录和未验证范围。测试数字来自实际执行或明确日期的历史日志，不作为吞吐量、业务收益或稳定性承诺。

## 可复现命令

从仓库根目录执行。使用 `.env.example` 配置独立的 `probe_test` 和 `probe_platform_test`；不要把测试 URL 指向演示库或实际平台库。

```bash
.venv/bin/python -m pytest --collect-only -q -p no:cacheprovider
.venv/bin/python -m pytest --require-postgres -q
.venv/bin/python -m pytest --require-postgres --junitxml=test-results/regression.xml
.venv/bin/python -m alembic current
.venv/bin/python -m alembic check
```

前端检查：

```bash
cd frontend
npm ci
npm run build
```

`--require-postgres` 在缺少数据库时明确失败。未加该选项时，部分 PostgreSQL 用例会跳过；必须报告 skipped，不能把契约测试的通过当作事务验证。

## 本轮实际验证（2026-11-10）

环境：macOS arm64、Python 3.14.4、PostgreSQL 17.9、Node.js 25.9.0、pytest 9.2.0。

| 检查 | 实际结果 |
| --- | --- |
| pytest 收集 | 194 tests collected；收集与执行分开记录 |
| 完整 PostgreSQL 回归 | **194 passed、0 failed、0 skipped**，保留 1 条既有 Starlette TestClient 弃用提示 |
| Alembic current | `0004_rerun_source (head)` |
| Alembic check | `No new upgrade operations detected`；仅覆盖平台迁移链 |
| 当前前端 build | TypeScript 检查与 Vite 生产构建通过 |
| 全新 Python 虚拟环境 | 从公共 PyPI 安装锁定依赖及可选 PostgreSQL 工具成功 |
| 全新本地 PostgreSQL | 在另一端口验证创建四份数据库、订单初始化、seed、平台迁移及 Alembic check |
| 全新后端运行 | 独立启动订单 API、平台 API 和 Worker；下单与库存查询成功，真实契约任务 6/6 通过并下载 JUnit |
| 全新前端依赖 | 独立目录执行 npm ci 和 build 成功；未将这项检查描述为新环境中的浏览器端到端验收 |
| Markdown 与 Mermaid | 相对链接/锚点/图片检查通过；两种语言 README 和文档的 6 个 Mermaid 图解析通过，主架构图本地渲染成功 |
| 中英文版本 | 9 组命令一致，测试日期/数字/边界同步，顶部语言切换链接有效 |
| ignore 规则 | 在临时 Git 仓库验证 12 项规则，实际工作目录的 Git 状态保持不变 |

全新运行使用独立的临时 PostgreSQL 实例和目录，没有复制真实 `.env`，没有指向用户正在使用的数据库。检查后该测试实例与自有后端进程已停止。上述结果是本轮本地验证，不代表性能、生产部署或云端 CI。

原始 JUnit 和日志保存在本地忽略的验证目录，公开文档只记录结果、环境与可复现命令。历史阶段结果见 [开发历史](development-history.md)。

### 尚未验证

- Docker Compose 配置已提供，本机没有 Docker，尚未实际启动该路径。
- Linux 已在 Ubuntu 24.04 CI 验证；其他 Linux 环境未逐一验证，当前 Worker 进程控制路径不支持 Windows。
- GitHub Actions [首次运行](https://github.com/Tsy-iii/Probe/actions/runs/42786973058)已通过：194 passed、0 failed、0 skipped，前端构建及迁移检查通过。环境为 Ubuntu 24.04、Python 3.14.10、Node.js 22.23.4、PostgreSQL 17.11；不代表生产部署验证。
- 浏览器接受检查使用过本地 Playwright 脚本，尚未提交正式 UI 测试体系。
- 两份主 README 的截图和 Mermaid 已在 GitHub 实际加载/渲染核对；其他文档的 GitHub 展示未逐页验收。

## 受控故障复盘验证（2026-11-10）

[三类复盘](postmortems/README.md) 使用现有测试独立复现，不修改业务代码。显式运行金额对照、commit 后响应丢失、SIGKILL Worker、旧写回 fencing 和双 schema 恢复，共 6 条检查，实际为 **1 failed、5 passed、0 errors、0 skipped**，1 条既有弃用提示。

唯一失败是故意错误金额实现，命令退出码 1 符合该实验约定；正确实现和其余检查通过。原始 JUnit 可解析并保留金额/响应丢失的 HTTP 与数据库证据；Worker 用例验证进程、数据和归属断言，没有采集完整 UI 时间线。此结果不是重新执行的完整回归，也不替代既有 194 passed CI 记录。

## 各类验证能证明什么

| 范围 | 关键验证 | 测试入口 |
| --- | --- | --- |
| HTTP 契约 | 参数、健康响应、请求 ID、显式故障 | `tests/test_contract.py` |
| PostgreSQL 业务 | 条件扣库存、实际 commit、竞争、rollback、支付与取消幂等 | `tests/test_orders.py` |
| 实际 TCP | 监听服务、连接与读取异常、服务日志及清理 | `tests/http/test_orders_http.py` |
| 任务与报告 | 真实 pytest 子进程、持久结果、部分报告与 JUnit | `tests/platform/test_runs.py` |
| 可靠性 | 并发领取、提交去重、取消、超时、旧所有者 fencing、Worker 恢复 | `tests/platform/test_reliability.py` |
| 用例范围与重跑 | 参数化目录、保存选择、拒绝过期范围、关联新执行 | `tests/platform/test_case_selection.py`、`test_rerun.py` |
| 证据 | 断言失败后 HTTP 和数据库证据仍保留 | `tests/platform/test_case_evidence.py` |
| 演示接口回归 | 双测试库隔离、取消/启动失败清理、替代 Worker 恢复 | `tests/platform/test_demo_suite.py` |

这些范围有重叠，不能把各层数量相加当作默认 pytest 收集数量。外层平台测试也会执行内层 pytest：外层通过可以表示平台正确保存了内层故意制造的失败。

## 数据与文件保留

订单测试使用 `probe_test` 的临时 schema。平台测试使用 `probe_platform_test`；工作台演示接口回归的两份空间由 Worker 管理。正常结束及受控恢复后检查 schema 不存在，并检查活动进程停止。

JUnit、日志和工作目录位于本次运行生成的临时目录或 `.local/runs/`，不包含在仓库中。清理标记不表示所有诊断文件已经删除。报告保存 HTTP／数据库观察结果，不能替代完整库存审计流水。

## 未验证范围

本地多 Worker 验证不代表生产分布式服务。生产部署、容器资源限额、跨主机故障、网络分区、容量和性能、真实支付及任意不可信测试仍不在当前验证结论中。
