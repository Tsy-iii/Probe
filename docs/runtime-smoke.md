# 完整启动与任务执行冒烟验证

这项检查回答一个具体问题：通过统一入口启动 Probe 后，前端代理、平台 API、PostgreSQL 队列和 Worker 能否完成一次真实任务，并保留可查询的结果？

脚本是 `scripts/runtime_smoke.py`。它使用 HTTP 请求访问真实服务，检查以下流程：

1. 启动订单 API、平台 API、Worker 和 Vite，四个组件都由本次验证拥有。
2. 再次执行 start，进程与归属记录保持一致。
3. 经 Vite 的 `/api` 代理查询套件、提交一个新的 `contract` 任务。
4. 观察同一个任务到结束，不重复提交或重跑失败任务。
5. 核对状态、六条用例、结构化报告、实际 pytest 日志与下载的 JUnit。
6. 查询 PostgreSQL，核对持久化计数，以及执行尝试的 Worker 是否对应启动器创建的进程。
7. 执行 stop，核对归属记录为空、应用进程消失和三个应用端口不再响应。

任务结束会释放当前 Worker 字段，执行尝试仍保留其历史 Worker ID；验证通过执行尝试定位归属。

## 本地执行

先按 README 准备 `.venv` 和前端依赖。默认本地模式还需要可选的 PostgreSQL 二进制：

```bash
.venv/bin/python -m pip install -r requirements-local-db.txt
.venv/bin/python scripts/runtime_smoke.py
```

每次执行复制实现文件到新的 `.local/smoke-<短ID>/` 目录，通过环境配置选择空闲端口，并创建一个新的真实 PostgreSQL 实例和四份数据库。源码内容不做端口替换。原项目的 `.env` 不读取、不复制，演示库和正在运行的手动服务不参与这项验证。

依赖安装由验证目录共享，运行目录、状态、日志和任务产物分开。Vite 的优化缓存写入工作目录里的 `.local/vite-cache`；启动器使用 runner 加载配置，避免通过共享依赖目录存放配置临时文件。

本地 PostgreSQL 仅监听回环地址，使用本次独立目录与空闲端口，临时实例采用 trust 认证。它只服务于本地冒烟，不是部署配置。缺少二进制或依赖时明确失败，不使用 SQLite 或内存数据库代替。

## 验证失败时的清理

显式制造一个“服务已经启动，但验收主动失败”的场景：

```bash
.venv/bin/python scripts/runtime_smoke.py --fail-after-start
```

预期退出码为 **1**，`summary.json` 的 `success` 为 false，并明确记录主动失败。清理成功时，`cleanup.applications_stopped` 和本地模式的 `cleanup.postgres_stopped` 为 true。这不能被报告成冒烟通过。

正常失败、Python 异常、Ctrl+C 与 SIGTERM 会进入清理流程。只调用本次独立工作目录的停止入口和 PostgreSQL 数据目录，不发送信号给未知进程。SIGKILL 无法执行 Python finally；CI runner 的销毁仍负责其临时环境。

如果 Worker 被强制停止或归属无法验证，启动器会返回非零，冒烟也判为失败。清理标记表示自有进程已经停止，工作目录、数据库文件和诊断产物会保留。

## 结果与证据

每次运行生成独立产物目录：

```text
test-results/runtime-smoke/<UUID>/
  summary.json       # 实际步骤、任务结果和清理状态
  run.json           # 查询到的真实任务报告，任务完成后生成
  junit.xml          # 从 API 下载的 JUnit，生成后保留
  task.log           # API 返回的 pytest 输出
  order.log
  platform.log
  worker.log
  frontend.log
  ...                # 初始化与各次启动／停止的命令日志
```

从任意目录调用脚本时，默认产物位置仍在脚本所属项目中。需要指定位置时使用 `--output-dir`；该目录下每次创建新的 UUID 子目录，不覆盖历史文件。

预期的六条契约用例是当前版本的验收条件，报告数字来自实际结果。新增契约用例时，应审阅并同步这一条件；它不是预填的成功率或性能数字。

## GitHub Actions

CI 在原有 PostgreSQL 回归 job 中准备虚拟环境和 Node.js／前端依赖，然后执行：

```bash
.venv/bin/python scripts/runtime_smoke.py --ci
```

CI 模式只接受 GitHub Actions 的显式数据库环境值和同一个回环 PostgreSQL service，要求平台历史为空，使用默认应用端口 8002／8001／5174。数据库由当前临时 job 创建和销毁，脚本只停止自己启动的四个应用组件。

冒烟的产物随后端 `test-results/` 一起上传，保留期为 14 天。完整 pytest 回归和这次契约任务分别报告；不能将内层六条和回归条数相加，冒充 pytest 默认收集数。

这项检查覆盖启动与服务连接、任务执行和证据导出。页面点击、布局和浏览器行为不在其验证范围内。它也不替代订单事务、并发与 Worker 故障恢复的专门回归。
