# 公开发布检查

项目已发布到公开仓库 [Tsy-iii/Probe](https://github.com/Tsy-iii/Probe)，MIT 与版权署名已经接受。本文区分已完成的发布验证与仍需维护者持续检查的事项。

## 本轮只读分析

- 已核对队列、lease、fencing、用例收集与选择、隔离、TCP 服务、证据和导出实现。
- 当前项目已初始化 Git，并推送公开仓库 Tsy-iii/Probe；首次发布已核对 133 个文件，真实 .env、数据库、日志和本地验收未进入远程仓库。
- `.env` 存在，但不应发布；`.local/` 中数据库、运行产物、浏览器验收和本次待审稿也不发布。
- 公开候选文本扫描未匹配常见 GitHub token 或私钥特征；这不替代人工检查 `.env`、日志和截图。
- npm lockfile 中依赖下载域名为公开 npm registry，没有发现内部 registry。
- 文档中的个人目录、外部工作区引用、具体进程编号和固定本地任务链接已移除或改成可复现步骤。
- 现有历史截图已目视检查；它们不是当前界面说明。主 README 使用本轮明确审阅的新截图。

## 本轮验证结果（2026-11-10）

| 项目 | 结果 |
| --- | --- |
| 中英文 README | 对应同版本内容，9 组命令、验证日期和数字一致 |
| 链接与图片 | 相对路径、锚点与图片存在性检查通过 |
| Mermaid | 6 个图本地解析通过，两种语言的主图渲染成功；两份主 README 架构图已在 GitHub 实际渲染并核对 |
| 完整回归 | 194 passed、0 failed、0 skipped，1 条既有 TestClient 弃用提示 |
| 迁移 | 平台位于 `0004_rerun_source`，Alembic check 无新增升级操作 |
| 前端 | 当前依赖和全新 npm ci 环境的 build 均通过 |
| 干净环境启动 | 公共 PyPI 锁依赖安装成功；独立 PostgreSQL、API、Worker 和契约任务已验证，自有进程已停止 |
| 隐私 | 公开文档扫描未发现个人目录、工作区内部引用、具体进程编号或常见 token/私钥特征 |
| Git | 临时仓库验证 12 项 ignore 规则通过；已核对本地 staged files 和远程树，私人配置、数据库与本地验证产物未发布 |

详见 [验证说明](verification.md)。本轮不发布原始日志、数据库、验证环境或待审差异。

## 持续维护与未验证范围

1. 后续每次提交继续核对文件，避免加入真实配置、数据库或私人日志。
2. Docker Compose 的直接启动路径仍未实际验证；Ubuntu 24.04 CI 的 PostgreSQL service 已验证，二者范围不同。
3. 其他 Linux 环境、Windows、生产部署和正式 Playwright UI 回归仍不在当前验证范围。
4. GitHub 的图片已实际加载核对；Mermaid 本地解析/渲染已通过，两份主 README 的 Mermaid 已实际渲染并核对包含平台节点；其他文档仍以本地解析结果为准。
5. [首次 CI](https://github.com/Tsy-iii/Probe/actions/runs/42786973058)已通过，后端 194 passed、0 failed、0 skipped，前端和迁移检查通过；徽章只显示真实动态状态。

本项目使用 Coding Agent 辅助开发，README 后部如实说明；公开介绍不宣称生产落地、性能收益、安全沙箱或 exactly-once。
