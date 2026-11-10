# 运行环境检查：先区分依赖问题与测试失败

> 当前入口见 [主 README](../README.md)；文末旧验收数字和截图为有日期的历史记录，不代表当前服务状态。`.local/` 与 `test-results/` 仅为运行产物位置，历史原始文件不随仓库发布。

入口：http://127.0.0.1:5174。点击测试工作台顶部的「Worker 在线 ⌄」状态，打开运行环境检查抽屉。连接异常时这个入口仍可点击。

## 可以直接体验

1. 打开工作台，点击顶部在线状态。界面会实际读取本次检查结果，显示检查时间与当前所选套件的条件。
2. 查看平台数据库、独立测试数据库、Worker 心跳、报告目录和手动订单 API。点击「检查证据」查看原始判断依据；顶部的请求关联可对应 `.local/platform.log` 中的 request_id。
3. 选择「接口契约测试」，再打开检查；它不依赖订单测试数据库。选择「订单 API 完整测试」时，需要独立 PostgreSQL 条件。当前 Worker 按套件的 requires_postgres 配置准备 schema，所以即使在订单套件内只选契约用例，该套件仍需测试数据库。
4. 修复环境后点击「重新检查」。检查是快照，不在抽屉中持续后台轮询。Esc 关闭抽屉；专注结果模式下，第一次 Esc 只关闭抽屉，仍保留专注模式。

## 状态具体是什么意思

| 状态 | 含义 |
| --- | --- |
| 正常 | 该项实际探测条件通过 |
| 等待 | 未发现近期 Worker 心跳，已接收任务可以入队等待 |
| 不可用 | 连接、结构、迁移、权限或文件读写等检查发现问题 |
| 未知 | 无法取得足够信息，例如读不到数据库里的 Worker 心跳 |

平台 API 检查接口有响应，不等于所有业务接口可正常写数据库。平台数据库不可读时，环境接口仍尽量返回其他组件状态，Worker 显示未知，不能直接说进程已停止。

「基础运行环境就绪」表示公共执行条件与独立测试数据库条件都通过；测试数据库不可用而公共条件正常时，显示「部分套件具备运行条件」，无数据库的契约套件仍可通过基础条件检查。

环境可用不等于断言通过。金额故障演示可以在正常环境下真实失败，失败原因应从断言和证据中判断。

## 每项检查怎样实现

| 项目 | 实际检查 | 判断范围 |
| --- | --- | --- |
| 平台数据库 | PostgreSQL 连接、必要表／列、Alembic 当前版本、SELECT／INSERT／UPDATE 权限、默认只读设置 | 基础结构与写入配置；不替代完整回归或约束／索引检查 |
| 独立测试数据库 | 实际库名为 probe_test、连接成功、CREATE 权限、默认只读设置 | 检查是否允许准备临时 schema；本检查不创建 schema，也不验证业务事务 |
| Worker | PostgreSQL 中最后报告状态、最近心跳与租约窗口，实际执行中任务数 | 近期心跳在线；不保证 OS 进程永远存活或所有 Worker 配置完全相同 |
| 报告目录 | 在配置目录内创建随机临时文件，写入、读取并删除 | 实际目录读写能力；缺失目录不在检查时自动创建 |
| 手动订单 API | 对配置的 loopback 地址发送 GET /health/ready，要求 200 与 ready | 用于 Swagger 手动演示；当前 TestClient 自动测试不依赖 8002 服务 |

数据库查询运行在只读事务中。报告目录的临时文件前缀为 `.probe-environment-`，正常关闭立即删除；不会清空报告、修改订单或创建测试任务。

检查针对平台 API 进程的配置。Worker 是独立进程，如果启动时使用不同环境变量，检查无法保证它与 API 完全一致。后续仍应实际提交测试并核对结果。

## 接口与配置

`GET /api/environment` 返回每项的 status、code、message、action、details、checked_at、duration_ms，以及整体状态、can_submit 和各套件的 execution_ready／blocked_by。探测耗时来自实际测量，不是性能测试指标。

单项依赖失败也返回 200 与结构化检查结果；HTTP 200 仅说明取得了检查结果，不表示环境就绪。平台 API 本身无法访问时，前端说明无法获取结果，其他依赖显示未知。

`can_submit` 表示平台数据库基础检查通过。环境检查提供诊断提示，不改变原有提交事务、排队、取消或恢复规则；Worker 不在线时仍允许保存 queued 任务。

相关配置：

```text
PROBE_PLATFORM_DATABASE_URL       平台任务库
TEST_DATABASE_URL                独立订单测试库 probe_test
PROBE_PLATFORM_ARTIFACTS_DIR      报告目录，默认 .local/runs
PROBE_PLATFORM_ORDER_API_BASE_URL 手动服务地址，默认 http://127.0.0.1:8002
```

手动服务地址限定为本机 loopback HTTP 地址和合法端口，不接受 HTTP 请求参数指定目标。响应不包含数据库连接 URL 或密码；异常只提供错误类型与明确处理步骤。

修改 `.env` 不会自动更新已经启动的进程。配置变更后需要重启平台 API，并让 Worker 使用相同配置；如果只是恢复数据库服务或启动 Worker，则重新检查即可。

本次不修改表结构，无需新增迁移。平台仍使用 `0004_rerun_source`。启动 API 和 Worker 后按上述流程体验。

## 故障怎样安全体验

先运行环境集成测试，它们不会停止当前服务：

```bash
# Run from the repository root
source .venv/bin/activate
python -m pytest tests/platform/test_environment.py --require-postgres -v
```

这些用例在 probe_platform_test 的随机 schema 中准备平台表，使用 probe_test 独立业务数据，并在随机本地端口启动真实订单 API。不可连接的地址、旧心跳、缺失列／版本、默认只读连接、文件路径和目录权限故障都由本用例确定性准备，结束后清理。

如果以后要手动观察 Worker 离线，先等当前任务结束，在你自己运行 Worker 的终端按 Ctrl-C；超过当前租约窗口后重新检查，应显示等待。重新运行 `python -m probe.platform.runner`，再检查即可恢复。当前这次开发没有停止正常 Worker 来制造故障。

## 演示回归的附属测试库

`demo_orders` 还需要 `probe_platform_test`，由 `PLATFORM_TEST_DATABASE_URL` 配置。环境面板单独检查连接与 schema 创建权限；该项故障只阻断需要它的套件，其他套件按各自依赖判断。

## 关键文件

- `probe/platform/environment.py`：独立检查各项，并发探测，保留不可用与未知的区别。
- `probe/platform/main.py`：公开结构化状态接口，关联请求 ID。
- `frontend/src/EnvironmentPanel.tsx`：按当前套件展示条件、故障步骤与检查证据。
- `frontend/src/DataDrawer.tsx`：复用已有抽屉，保持工作台和数据页布局。
- `tests/platform/test_environment.py`：真实数据库、HTTP、文件读写与确定性故障验证。

浏览器验收文件在 `.local/browser-check/`。故障展示使用独立 TestClient 检查应用实际返回的故障响应，并在浏览器上下文中拦截它们验证显示；不会改动正常 `.env` 或将模拟展示当作当前主环境故障。

## 本次实际结果

2026-11-09 本机环境检查新增 12 条集成验证。完整回归 `python -m pytest --require-postgres -q`：103 passed、0 failed、0 skipped，1 条 Starlette TestClient 弃用提示。目录权限不足场景通过实际权限位验证；以 root 运行时该场景会如实跳过，不能据此证明权限检查。

`npm run build` 通过。浏览器验证正常状态、平台数据库不可用时 Worker 未知、测试数据库故障下不同套件条件、检查接口不可访问时其他依赖未知，以及键盘、专注模式、平板和手机布局。抽屉打开前后报告区尺寸保持一致。

31 个已有任务详情、报告和尝试与开发前逐条核对一致；演示数据保持不变。测试 schema 与报告目录检查临时文件均无残留。本次检查结果不作为新的业务性能数据。
