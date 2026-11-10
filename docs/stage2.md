# 第二阶段：可视化测试任务与报告

> 历史里程碑文档。当前启动以 [主 README](../README.md#quick-start) 和 [中文导航](README.zh-CN.md) 为准；本页的阶段结果只对应当时环境，不是当前运行状态。原始本地产物不随仓库发布。

**本文记录第二阶段的设计。当前已进入第三阶段：队列和 Worker 架构已升级，启动时请使用 [第三阶段指南](stage3.md)，尤其需要单独启动 Worker。** 第二阶段的验收记录仍保留用于比较设计取舍。

当前可以从 React 页面提交一个测试任务，观察待执行／执行中／结果，展开真实用例报告与失败断言，读取 pytest 输出并下载 JUnit XML。本阶段不接入 AI，不生成虚构测试数据。

## 从页面理解数据流

```mermaid
flowchart LR
    UI[React 页面 5174] -->|提交 suite_id| API[平台 API 8001]
    API -->|事务提交 queued| DB[(probe_platform)]
    API --> Queue[单进程 FIFO 队列]
    Queue --> Runner[单执行槽]
    Runner -->|启动子进程| Pytest[pytest]
    Pytest --> TestDB[(独立 probe_test)]
    Pytest --> Files[JUnit XML 与输出日志]
    Files -->|解析实际结果| DB
    DB -->|状态与报告| UI
```

HTTP 202 代表接收成功，不表示测试通过。提交时生成 run ID；后台启动后改为 running，pytest 结束、报告解析完成后再保存最终结果。页面每 2 秒读取任务状态，正在查看的执行中任务每 1 秒读取详情与日志。

第一阶段的测试通过 TestClient 调用订单应用，使用真实 PostgreSQL；完整订单测试无需访问演示服务 8002，也不会修改它使用的 probe 库。8002 和 Swagger 继续用于手动理解订单业务。

## 地址与数据库

| 用途 | 地址／数据库 |
|---|---|
| 可视化执行台 | http://127.0.0.1:5174 |
| 平台 API 文档 | http://127.0.0.1:8001/docs |
| 订单 API 文档 | http://127.0.0.1:8002/docs |
| 平台任务与结果 | probe_platform |
| 平台接口测试 | probe_platform_test，每例独立 schema |
| 订单自动化测试 | probe_test，每例独立 schema |
| 手动演示订单 | probe |



## 本地启动

从项目根目录开始。新环境先按 README 创建虚拟环境、安装 requirements.lock.txt；已有环境继续使用原来的 .venv。

```bash
# Run from the repository root
source .venv/bin/activate
python -m probe.local_postgres start
python -m probe.platform.manage init-db
python -m uvicorn probe.platform.main:app --host 127.0.0.1 --port 8001 --workers 1
```

本地 PostgreSQL 启动命令会补建缺少的 probe_platform 和 probe_platform_test，不重置其他数据库。平台建表为显式 create_all，重复运行保留既有任务；没有 schema 升级操作，后续变更已有结构时应引入迁移。

另开一个终端启动前端：

```bash
cd frontend
npm ci
npm run dev
```

Vite 固定绑定 127.0.0.1:5174，端口被占用时直接报错，不自动改到其他端口。开发与预览的 /api 请求代理到 8001，因此前端无需额外配置跨域。

本次交付已启动平台和前端，可以直接打开 5174，不必再次启动占用相同端口。日志为 `.local/platform.log` 和 `.local/frontend.log`。如果要接管到自己的终端，先用 `lsof -nP -iTCP:8001 -iTCP:5174 -sTCP:LISTEN` 确认对应 Probe Python／Vite 进程，再对它们执行 `kill -TERM 对应PID`，然后按上面的步骤启动。自己在终端启动后使用 Ctrl-C 停止；平台正常关闭会等待已提交任务完成。

构建：`npm run build`；本地查看构建产物：`npm run preview`，仍使用 5174。先停止开发服务器再运行 preview。

`.env.example` 新增的配置：

```dotenv
PROBE_PLATFORM_DATABASE_URL=postgresql+psycopg://probe:probe_local@127.0.0.1:5433/probe_platform
PLATFORM_TEST_DATABASE_URL=postgresql+psycopg://probe:probe_local@127.0.0.1:5433/probe_platform_test
```

原有 TEST_DATABASE_URL 继续指定订单测试库 probe_test。若使用不同数据库端口，四个 URL 都要指向正确端口。平台拒绝使用演示库保存任务，也拒绝用演示库执行订单自动化测试。

Docker 用户：新的空数据卷会按初始化 SQL 建四个库；已有第一阶段数据卷不会再次执行初始化脚本，需要显式补建下面两个库，无需删除卷：

```bash
docker compose exec postgres psql -U probe -d postgres -c 'CREATE DATABASE probe_platform OWNER probe;'
docker compose exec postgres psql -U probe -d postgres -c 'CREATE DATABASE probe_platform_test OWNER probe;'
```

本机仍没有 Docker，Compose 启动路径未实测。

## 一次成功与一次失败

1. 打开 5174，选择「订单 API 完整测试」，点击「提交测试任务」。202 接收后，左侧出现新任务；观察待执行、执行中、已通过。
2. 点击该任务。右侧展示实际总数、通过／失败／错误／跳过数、每条用例耗时。展开一条用例，查看名称；切换日志，阅读 pytest 输出。
3. 选择「固定错误演示」，再提交一次。这个套件明确预期失败：在订单应用显式注入固定 503，测试仍断言创建应返回 201。
4. 在失败报告中找到 Expected 201、503、injected_error 和 failure-demo-001。这些是本次真实响应与断言，不是 AI 推测。
5. 下载 JUnit XML，刷新网页，再查看已完成记录。记录在 PostgreSQL、文件在 .local/runs，各自保留。

故障演示没有访问实际数据库；正常运行 pytest 默认只收集 tests/，不会自动执行这个故意失败的 examples/ 文件。平台不会把“预期失败”改成通过，页面仍真实显示 failed。

## 平台接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /health | 平台进程健康 |
| GET | /health/ready | 数据库与任务表可查询 |
| GET | /api/suites | 获取固定可执行套件 |
| POST | /api/runs | 提交任务，返回 202 和 queued 记录 |
| GET | /api/runs | 最近任务与来自数据库的实际状态统计，limit 为 1～100 |
| GET | /api/runs/{run_id} | 状态、时间、退出码、计数、逐用例报告 |
| GET | /api/runs/{run_id}/log | 输出日志；过长时显示尾部 256 KiB |
| GET | /api/runs/{run_id}/junit | 下载报告；尚无报告返回 409 |

提交请求：

```bash
curl -i -X POST http://127.0.0.1:8001/api/runs \
  -H 'Content-Type: application/json' \
  -d '{"suite_id":"stage1"}'
```

suite_id 仅允许 stage1、contract、failure_demo。调用者不能传 shell 命令、任意测试路径或额外字段。run ID 是 UUID，不存在的任务返回明确 404。

## 状态、计数与证据

| 状态 | 含义 |
|---|---|
| queued | 数据库已记录，等待本进程执行 |
| running | 执行器已开始任务 |
| passed | pytest 返回 0，报告中至少有一个通过用例且没有失败／错误 |
| failed | pytest 返回 1，报告包含失败断言或测试阶段错误 |
| error | 启动失败、报告缺失／损坏、其他退出码或无法确认有效测试结果 |

未知的计数保存为 NULL，页面显示“结果待生成”或“无测试结果”，不填入虚构的 0 通过。报告由 JUnit testcase 元素计算，分别统计 passed、failed、error、skipped；全部跳过不会被认定为 passed。例外启动错误保留 error_message，仍可查看任务。

耗时使用实际开始／结束时间，用例耗时读取实际 JUnit time 字段。任务数量统计从数据库查询，包括明确标注的失败演示；没有预填成功率或业务收益指标。

每个任务文件目录：`.local/runs/<run_uuid>/output.log` 和 `junit.xml`。接口只按已存在的 UUID 读取固定文件名，页面用文本展示日志与断言，不将内容作为 HTML 执行。

## 模块与取舍

| 模块 | 解决的问题／实现原因 | 如何验证 | 边界 |
|---|---|---|---|
| models.py / repository.py | 任务状态、时间和报告持久化；先提交 queued 再排队 | HTTP 与真实 PostgreSQL 记录核对；重启已完成记录测试 | 单表 JSONB 存逐用例报告，适合按任务读取；跨任务用例分析尚未实现 |
| runner.py | 在后台运行 pytest；固定 argv、sys.executable、不使用 shell；单槽 FIFO | 实际子进程成功、失败、顺序及启动异常测试 | 仅本进程内队列；不是持久队列或沙箱 |
| reports.py | 从实际 JUnit 解析计数、断言、跳过与耗时 | 正常和故障套件的计数、XML 下载和失败文本断言 | 报告最大 10 MiB、单例详情截取 20000 字符；完整输出另存在日志 |
| main.py | 202 提交、状态／报告／日志 API、请求 ID | 参数拒绝、404／409、真实任务流程 | 本地单用户，无登录、权限、任意用例上传 |
| frontend | 选择套件、自动刷新、历史记录、报告与日志 | TypeScript 构建与实际 Chrome 操作检查 | 无在线编辑器、调度、取消按钮或复杂后台 |
| failure demo | 让失败有确定的可观察原因 | 断言 201 与实际 503、request ID、XML／日志 | 故意失败且单独选择；默认测试不收集 |

## 当前执行边界

平台必须以一个 Uvicorn worker 运行，不使用 --reload。正常关闭时停止接收新任务并等待已提交任务逐个完成。没有任务超时，因此若 pytest 挂住，单执行槽也会等待；取消、超时和进程清理安排在第三阶段。

内存队列会在进程被强制杀死后丢失；数据库可能留下 queued／running。第二阶段不会自动恢复这些未完成任务，也不会自动重试。已经完成的记录与文件会保留。新进程仍可接收新任务。

数据库提交与内存入队之间没有跨系统原子性。此设计便于先理解异步执行和持久结果；第三阶段再解决可靠领取、去重、持久队列和 Worker 异常恢复。每次运行采用独立子进程、独立文件目录和测试 schema，这是基础数据隔离，不是安全沙箱或容器级资源隔离。

## 测试与学习

完整回归：

```bash
python -m pytest --require-postgres -v --junitxml=test-results/stage2.junit.xml
```

单独平台 API 测试：`python -m pytest tests/platform --require-postgres -v`。平台测试也采用独立 PostgreSQL 库、每例独立 schema 和临时文件目录；需要观察任务结束时轮询状态，不重试已经失败的任务。

本次实际结果与页面检查见 [第二阶段验证记录](stage2-verification.md)。

先读一条链：schemas.SubmitRun → main.submit → repository.create → runner._execute → reports.parse_junit → repository.finish → 页面结果与证据。

三个短练习：

1. 提交完整套件，解释为什么 POST 返回 202 时计数还是 NULL，为什么它不能直接返回“测试通过”。
2. 连续提交正常和失败演示，比较 started_at／finished_at，验证单槽 FIFO；指出这和持久队列的差别。
3. 在失败报告中从状态 failed 找到具体断言、HTTP 503 和请求 ID，再到日志中找到对应输出。

面试可以展开：接收任务与完成任务为何是两种状态？如何保证失败报告可追溯，并区分测试失败与执行异常？先用本阶段真实例子解释，再讨论第三阶段需要补上的可靠性设计。
