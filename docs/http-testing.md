# 真实 HTTP 订单测试

> 当前入口见 [主 README](../README.md)；文末旧验收数字和截图为有日期的历史记录，不代表当前服务状态。`.local/` 与 `test-results/` 仅为运行产物位置，历史原始文件不随仓库发布。

这个增量保留现有 TestClient 用例，增加通过真实 TCP 地址访问独立订单服务的测试。运行时不接入 AI，GitHub Actions 继续后置。

## 直接体验

1. 打开 http://127.0.0.1:5174，选择「真实 HTTP 订单测试」。点击「查看 / 选择」，当前有 13 条实际收集到的用例。
2. 先只选择 `test_http_create_query_and_pay`，超时设为 60，提交新任务。它通过 HTTP 购买 2 件商品、查询订单、支付，然后读取真实 PostgreSQL 核对金额 3980 分、库存 3、订单 paid 和一条事件。
3. 在报告展开用例，查看方法、路径、完整的本次 HTTP 地址、响应和数据库前后快照。每段证据在断言之前保存，失败时也保留。
4. 点击摘要里的「HTTP 服务」或「执行详情」，找到 HTTP 测试服务。查看本次服务地址、PID、所属用例、schema、就绪／停止时间和清理状态。服务正常结束后已停止，旧地址不能作为长期访问地址。
5. 展开一个服务，点击「查看 HTTP 服务日志」，观察请求 ID 与服务侧状态；点击日志工具栏「返回 pytest 日志」回到用例执行输出。
6. 单独选择 `test_http_read_timeout_has_no_response`。显式延迟 600ms、读取超时 50ms，预期用例通过，证据显示 ReadTimeout 和“无 HTTP 响应”。GET 查询不会写订单；数据库右侧快照标为“客户端异常时”。
7. 再选择 `test_http_connection_failure_has_no_response`。该用例预留一个未监听端口，并不启动订单服务；连接阶段得到 ConnectError 或 ConnectTimeout，记录实际类型，状态码保持 null。不同 OS 对绑定但未监听端口的行为可能不同。
8. 全选并提交，正常预期 13 条通过。13 条用例对应 12 次服务启动，连接故障用例只有端口保留。地址、日志和清理记录保留在各自尝试目录中。

验证留下的完整 HTTP 任务为 `<run-id>`。在历史中搜索 ID 前缀可以打开它；它的 13 条结果和 12 个服务状态都来自实际执行。

## 业务与故障范围

| 场景 | 核对内容 |
| --- | --- |
| 创建、查询、支付 | HTTP 响应、金额、库存、订单与支付事件 |
| 取消两次 | 库存只恢复一次 |
| 重复回调与冲突内容 | 重复事件去重，冲突返回 409，数据库不变 |
| 错误金额再正确支付 | 错误事件回滚，正确回调成功 |
| 数量 0／-1 | 每条只改变 quantity，422 定位该字段 |
| 库存不足／商品不存在 | 409／404 对应错误码，无订单残留 |
| 状态冲突 | 已支付不能取消，已取消不能迟到支付 |
| 8 请求并发竞争库存 5 | 5 次成功、3 次拒绝，库存 0、订单 5 |
| 固定错误 | 仅隔离服务显式开启故障，实际返回 503，业务数据不变 |
| 读取超时 | 无 HTTP 响应，实际 ReadTimeout |
| 连接故障 | 无 HTTP 响应，实际连接阶段异常 |

业务失败返回 409 或显式错误返回 503 时，只要符合用例契约，测试可以通过。客户端未拿到响应时不能写成 HTTP 0／503；也不能据此假定服务没有执行或事务已回滚。

并发用例的数据库证据对应整个批次，不能把同时发生的库存变化归因给某一条请求。请求读取超时的例子选择 GET，避免将 POST 的未知业务结果简单当作失败后可重试；创建订单目前没有请求去重，重复请求会新增订单。

## 启动与隔离

平台和 Worker 按 [第三阶段说明](stage3.md) 启动。完成启动后可从 5174 提交本次任务。

HTTP 测试无需手动启动 8002：每条用例在准备数据后启动自己的订单服务，数据库必须是独立的 probe_test，并使用 case_UUID／run_UUID schema。初始商品为 1990 分、库存 5，与手动演示库独立。

端口由操作系统通过 `bind(127.0.0.1, 0)` 分配，父进程传递已绑定 socket；避免查空闲端口后再次绑定的竞争，不占用固定演示服务端口。

正常 fixture 清理顺序是：关闭 HTTP 客户端 → 停止服务进程并等待 → 清理数据库表／schema。每条用例重新准备业务数据，平台运行时仍使用本次尝试的 run_UUID；最后由 Worker 删除整份 schema。

## 任务取消与超时体验

选择「HTTP 服务清理演示」，它在独立服务就绪后等待 8 秒：

1. 超时设为 30，提交，打开执行详情，看到服务就绪后点击取消。最终应为 cancelled，尝试清理完成，服务状态已停止。
2. 再提交一个新任务，超时设为 6。预期 timed_out，服务进程和 schema 被清理，没有完整 JUnit 时计数为 null。
3. 时间充足时演示正常通过 1 条，服务同样停止。它是平台显式选择的演示，不在默认 pytest 测试集合中。

请求超时只影响一条 HTTP 请求的等待；任务超时停止整次执行并清理。HTTP 服务继承本次尝试的 PROBE_ATTEMPT_ID，Worker／执行监视器按不可混淆的标记清理进程，沿用当前异常恢复机制，不盲杀历史 PID。

报告里的用例耗时还包含服务启动与 fixture 清理，不能把它直接当成某条 HTTP 请求的耗时。证据里的超时设置与实际异常类型分别描述等待配置和实际发生的阶段。

服务自身也监视 pytest 父进程，父进程消失后尝试退出。平台异常恢复会再次确认旧尝试进程停止与 schema 清理，再执行新尝试。直接终端 pytest 被 SIGKILL 时仍可能遗留 case_UUID，平台的 run_UUID 清理由 Worker 保障；不宣称直接进程终止时也能完成所有数据库清理。

## 保存的数据与接口

服务记录保存在 `.local/runs/<run_id>/<attempt_id>/http-services.json`，服务日志在同目录的 `http-services/<service_id>/server.log`。记录含真实分配地址、服务 PID、所属用例、启动／就绪／停止时间和清理状态，不保存数据库 URL 或密码。

任务详情 `GET /api/runs/<run_id>` 增加 http_services 与 http_service_notice。服务日志入口为：

```text
GET /api/runs/<run_id>/http-services/<service_id>/log
```

API 校验服务属于该任务，并由 UUID 构造固定日志路径，最多读取最后 256 KiB。辅助清单损坏时，实际报告继续可读并显示提示；清单写回失败不能阻止实际清理。停止时间没有记录时保持未知，不补造时间。

原有证据 JSON 导出包含服务地址／状态与网络请求证据；服务日志通过独立入口读取。未冻结源码版本，记录对应执行当时磁盘上的代码；POSIX socket 继承与进程控制已在本机 macOS 验证，Windows 不支持，Linux 尚未在本机实测。

## 关键代码

- `probe/http_service.py`：独立服务入口，限制测试库、schema、父进程与 loopback socket；只使用 fixture 已准备的表。
- `probe/http_testing.py`：启动／等待就绪／停止服务，记录地址清单；启动等待是 readiness 轮询，业务请求不重试。
- `tests/http/conftest.py`：每例服务 fixture、httpx 客户端和断言前证据采集。
- `tests/http/test_orders_http.py`：13 条真实 HTTP 用例，核心断言仍由 Python 编写。
- `tests/platform/test_http_execution.py`：平台执行、日志、正常／取消／超时／Worker 恢复及多 Worker 清理验证。

## 运行验证

```bash
# Run from the repository root
source .venv/bin/activate
python -m pytest tests/http/test_orders_http.py --require-postgres -v
python -m pytest tests/platform/test_http_execution.py --require-postgres -v
python -m pytest --require-postgres -q
```

首次 HTTP 验证 12 通过、1 失败：本机绑定未监听端口返回 ConnectTimeout，与只接受 ConnectError 的用例预期不符。根据实际行为修正为连接阶段故障契约，并保留具体异常类型；没有重试业务请求来掩盖错误。

最终实际结果（2026-11-09）：完整回归 123 passed、0 failed、0 skipped，1 条 Starlette TestClient 弃用提示；其中新增 13 条 HTTP 用例与 7 条平台 HTTP 执行／清理验证。前端构建通过。

平台实跑任务 `<run-id>` 实际 13 条通过、12 个服务停止。验证覆盖正常、取消、超时、Worker 丢失恢复、双 Worker 地址／schema 隔离，以及辅助元数据写回失败后仍完成清理。浏览器验证实际地址、空响应的异常类型、服务／pytest 日志切换及手机布局。已有 31 个任务报告与尝试保持不变，演示数据不变，测试服务和临时 schema 清理后无残留。

浏览器验收截图与脚本在 `.local/browser-check/`，仍未接入正式 Playwright CI。

## 两个小练习

1. 阅读 `test_http_create_query_and_pay`，说明 HTTP 地址从哪里来、数据为什么没有出现在演示库，以及支付后库存为什么不再次减少。
2. 将读取超时从 0.05 秒改为 1 秒，先预测原来的“应抛 ReadTimeout”断言会怎样；再为 600ms 延迟写正常响应断言，运行后恢复或保留为独立用例。
