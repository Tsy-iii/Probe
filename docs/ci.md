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

工作流已配置，首次云端结果待实际推送后确认。通过或失败均以 GitHub Actions 记录为准，尚未填写成功数字或通过徽章。
