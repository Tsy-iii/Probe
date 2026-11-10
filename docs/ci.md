# GitHub Actions

仓库：[Tsy-iii/Probe](https://github.com/Tsy-iii/Probe)。工作流：[CI](https://github.com/Tsy-iii/Probe/actions/workflows/ci.yml)，配置在 `.github/workflows/ci.yml`。

## 第一版检查范围

| Job | 执行内容 |
| --- | --- |
| PostgreSQL regression | Ubuntu 24.04、Python 3.14、PostgreSQL 17；创建四份独立用途的数据库，初始化订单表与平台迁移，运行 Alembic check 和完整 pytest |
| Frontend typecheck and build | Ubuntu 24.04、Node.js 22；npm ci、TypeScript 检查和 Vite 构建 |

测试必须带 `--require-postgres`，数据库缺失会失败。平台测试仍使用临时 schema；CI 的数据库、账号密码和运行数据只属于这次临时环境，不读取真实 `.env`。

## 触发与产物

- 推送到 main、Pull Request 和手动 workflow_dispatch 可以触发。
- 只修改 Markdown、文档图片或 LICENSE 时跳过自动运行；需要时可手动触发。
- 同一分支／PR 的新执行会取消过时 CI，减少重复等待。
- 后端尝试在成功或失败后保存 JUnit 和 pytest 日志，保留 14 天；依赖安装前失败时可能没有测试产物。
- 前端成功构建后保存 dist，保留 14 天；保存构建产物不代表网站已部署。
- Actions 引用固定到已核对的官方提交 SHA，工作流仅声明 contents: read。

## 怎样看结果

打开仓库 Actions → CI → 对应执行，分别查看两个 job。后端失败先看失败步骤和 pytest.log，再看 JUnit 中的具体断言；不要把前端构建通过当成接口测试通过。

CI 验证的是当前版本代码在指定环境下的测试结果。它不证明性能、生产可用性、任意网络分区或安全沙箱。第一版不包含正式 Playwright UI 回归、部署、定时调度或运行时 AI。

## 首次运行状态

2026-11-10：[首次运行](https://github.com/Tsy-iii/Probe/actions/runs/42786973058)通过，run attempt 为 1，没有通过重跑掩盖失败。

| 检查 | 实际结果 |
| --- | --- |
| 后端 | 194 passed、0 failed、0 skipped，1 条既有 TestClient 弃用提示 |
| 迁移 | 平台 head 为 `0004_rerun_source`，Alembic check 没有新升级操作 |
| 前端 | npm ci、TypeScript 和 Vite 构建通过 |
| 运行环境 | Ubuntu 24.04，Python 3.14.10，Node.js 22.23.4，PostgreSQL 17.11 |
| 产物 | 后端 JUnit／pytest 日志和前端 dist 已上传并下载核对 |

验证的代码提交为 `5b9df75272f8f7b63a87397fb1fd9e42aec3b32c`。后续只更新 Markdown 说明的提交按 paths-ignore 跳过自动执行，不改变本次验证的程序代码。CI 结果不作为性能或生产容量承诺。
