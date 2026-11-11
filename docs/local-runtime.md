# 本地启动、状态与停止

Probe 由订单 API、平台 API、Worker 和前端四个进程组成。数据库、依赖和表结构准备好后，可以在仓库根目录使用统一入口：

```bash
./scripts/probe start
./scripts/probe status
./scripts/probe stop
```

`start` 在后台启动缺少的组件并等待它们就绪，随后终端返回。关闭这个终端不会停止后台服务。`status` 检查实际状态；`stop` 只关闭这个入口自己启动的服务。

## 第一次使用

1. 按 [English README](../README.md#quick-start) 或 [中文 README](../README.zh-CN.md#快速开始) 安装 Python 和前端依赖，创建 `.env`。
2. 启动 PostgreSQL，准备四份数据库。依次执行订单初始化、演示数据 seed 和平台初始化。
3. 从仓库根目录执行 `./scripts/probe start`。
4. 执行 `./scripts/probe status`，确认四个组件都显示“就绪”。
5. 打开工作台，运行“接口契约测试”，查看报告。
6. 使用完后执行 `./scripts/probe stop`，再查看 `status`。

统一入口不会安装依赖、启动 PostgreSQL、执行迁移或重置演示数据。这些准备工作仍是显式操作，避免一次启动覆盖已有数据或配置。

| 组件 / 入口 | 默认地址 | 怎样判断就绪 |
| --- | --- | --- |
| 订单 API | http://127.0.0.1:8002/docs | 健康接口、演示库名、public schema 和 development 环境 |
| 平台 API | http://127.0.0.1:8001/docs | 健康接口、平台身份和数据库快照来源 |
| Worker | 无网页端口 | 对应进程在 PostgreSQL 中有近期在线心跳 |
| 前端工作台 | http://127.0.0.1:5174 | 页面可响应且包含 Probe 标识 |
| 数据浏览 | http://127.0.0.1:5174/#data | 与工作台使用同一个前端 |

平台和前端端口固定为 8001 / 5174，以匹配现有 Vite 代理。订单端口由 `PROBE_PLATFORM_ORDER_API_BASE_URL` 配置，默认 8002。端口属于其他程序时，启动器报错并保留该程序；不会自动抢占端口。不会使用 Discovery 的 5173 端口。

## 已经开着平台时

如果四个组件已经在其他终端中手动运行，`start` 会验证进程的命令、工作目录和实际就绪状态，并显示：

```text
平台 API：复用手动服务，stop 不会关闭它
```

这时 `stop` 后页面仍能打开，是预期行为。那些组件仍由原来的终端管理，需要在那里按 Ctrl+C 停止。启动器不会为了“全部停止”接管它们，也不会关闭 PostgreSQL。

重复执行 `start` 会复用已经就绪的服务。仅仅发现进程存在不代表就绪；不健康的已有服务会阻止重复启动，并提示先检查日志。

## 观察日志

脚本启动的服务分别写入以下文件：

```bash
tail -n 80 .local/dev-runtime/order.log
tail -n 80 .local/dev-runtime/platform.log
tail -n 80 .local/dev-runtime/worker.log
tail -n 80 .local/dev-runtime/frontend.log
```

这些日志采用追加方式。手动启动的服务仍写入它们原来的终端，不会自动出现在这里。平台测试任务自身的报告、JUnit 和执行日志继续保存在原有的任务产物目录。

服务归属保存在 `.local/dev-runtime/state.json`，包含会话标识、进程 ID 和创建时间。发送停止信号前，还会核验进程继承的项目／会话／服务标记。不能只相信一个旧 PID，因为系统可能把它分配给另一个程序。

不要在服务运行期间删除或编辑归属文件。如果归属无法验证，启动器会拒绝停止；先核对状态与原启动终端。`.local/` 已被 Git 忽略，日志和本机归属记录不会作为源码上传。

## 失败与停止的行为

- **依赖或数据库未准备好：** `start` 给出明确提示。已有 `.env` 和数据保留；导出的环境变量优先于 `.env`。
- **启动中途失败或 Ctrl+C：** 尝试回收本次新建的进程，保留启动前已有的服务。
- **正常停止：** 优先停止 Worker，让它执行已有的中断处理和清理，再关闭前端与两个 API。不会删除历史报告或商品数据。
- **Worker 超过停止等待期限：** 强制停止并返回非零退出码，明确提示测试数据清理尚需恢复流程核对；不能把强制退出描述为已确认清理成功。
- **数据库已停止：** `stop` 使用本地归属记录，不需要连接 PostgreSQL；仍需要项目虚拟环境与 Python 依赖。

`start` 成功退出码为 0。`status` 只有四个组件全部就绪时返回 0；未运行、不健康或归属未知时返回非零。因此停止后 `status` 返回非零是正常结果。

## 验证与边界

```bash
# 真实临时 HTTP 进程、端口和信号的管理集成检查。
.venv/bin/python -m pytest tests/test_dev_runtime.py -q

# 同时执行现有 PostgreSQL 业务与平台回归。
.venv/bin/python -m pytest --require-postgres -q
```

`tests/test_dev_runtime.py` 检查重复启动、手动服务保留、端口冲突、部分启动失败、超时、中断、跨进程会话子进程清理和归属保护。它使用小型 HTTP 服务作为管理测试对象，不据此声称订单事务得到验证。真实订单事务由原有 PostgreSQL 测试覆盖。

这是 POSIX 本地开发工具，当前新增入口的实测环境为 macOS。它不是服务部署系统，没有开机自启、崩溃自动重启或日志轮转。进程标记用于可信本地服务的生命周期管理，不构成安全沙箱。

本轮执行结果与其他验证范围见 [验证记录](verification.md)。
