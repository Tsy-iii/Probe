# 在数据页体验订单 API

> 当前入口见 [主 README](../README.md)；文末旧验收数字和截图为有日期的历史记录，不代表当前服务状态。`.local/` 与 `test-results/` 仅为运行产物位置，历史原始文件不随仓库发布。

入口：<http://127.0.0.1:5174/#data> →「订单演示数据」。商品管理准备业务数据，订单操作调用实际接口，数据表显示刷新后的 PostgreSQL 快照。

## 一条完整操作流程

1. 在商品表点击「新增商品」，填写「流程体验商品」、单价 **29.90 元**、库存 **5**，保存。记下页面实际返回的商品 ID。
2. 点击新商品，只打开详情。在「商品操作」点击「创建订单」。窗口会通过订单 API 读取当前价格／库存；输入数量 **2**，预计金额为 **59.80 元**。点击「创建订单」。
3. 核对实际结果：`POST /orders` 返回 **201**，状态 `pending`，总金额 **5980 分**，读取到的库存为 **3**。下面「本次请求记录」展示真实请求 ID、请求体及响应。点击「查看订单详情 →」才切换到订单表。
4. 在待支付订单详情点击「取消订单」，确认。`POST /orders/{id}/cancel` 返回 **200**，状态 `cancelled`；操作后读取库存为 **5**。点击「重复取消（验证幂等）」后，库存仍是 **5**。
5. 回商品表，对同一商品再创建 **2** 件的订单。在这笔新订单详情点击「模拟支付」。保留自动生成的稳定事件 ID、默认金额 **59.80 元**，发送回调。
6. 核对 `POST /payments/callback` 返回 **200**，`order.status=paid`、`duplicate=false`，库存仍为 **3**。点击「重放同一支付回调」后应返回 `duplicate=true`，同一事件不会新增第二条记录，也不会再次扣库存。
7. 回数据表，查看商品、订单和支付事件。需要关联筛选时从详情的关联按钮进入；普通点击行只是查看详情。

上述数值是这组输入的预期结果，需以实际响应和刷新后的数据核对。多个浏览器同时操作时，当前商品价格／库存可能变化，订单总金额以服务在事务中实际使用的价格为准。

## 每个业务接口在做什么

业务接口在独立订单 API **8002**。页面通过平台 API **8001** 的固定入口调用它，页面不能指定任意地址。业务事务仍由订单服务完成。

| 业务方法与路径（8002） | 页面入口（8001） | 参数与用途 | 关键响应 |
| --- | --- | --- | --- |
| `GET /health` | 无，直接检查服务 | 无参数；只确认进程能响应，不检查数据库 | 200：`status=ok` |
| `GET /health/ready` | 每次演示调用前由平台检查 | 无参数；确认真实 PostgreSQL 就绪、库名、schema 和运行环境 | 200：ready；503：数据库不可用 |
| `GET /products/{product_id}` | `GET /api/demo/products/{product_id}` | 路径商品 ID；读取当前单价、库存和在售状态，操作前后核对用 | 200：商品；404：商品不存在 |
| `POST /orders` | `POST /api/demo/orders` | JSON：`product_id`、`quantity`；创建单商品订单并扣库存 | 201：pending 订单；404：商品不存在；409：库存不足、商品已删除、金额溢出；422：参数错误 |
| `GET /orders/{order_id}` | `GET /api/demo/orders/{order_id}` | 路径 UUID；读取当前订单状态、数量、下单金额和时间 | 200：订单；404：订单不存在；422：UUID 格式错误 |
| `POST /orders/{order_id}/cancel` | `POST /api/demo/orders/{order_id}/cancel` | 路径 UUID；**无请求体**；pending 取消后恢复库存 | 200：cancelled，包含重复取消；404：订单不存在；409：已支付不能取消 |
| `POST /payments/callback` | `POST /api/demo/payments/callback` | JSON：稳定 `event_id`、订单 UUID、`amount_cents`；验证金额并处理通知 | 200：`order`、`duplicate`；404：订单不存在；409：金额不一致、订单已取消或事件内容冲突 |

业务接口的参数／返回结构也写在 [订单 API 文档](http://127.0.0.1:8002/docs)，页面转发接口写在 [平台 API 文档](http://127.0.0.1:8001/docs)。每个操作窗口的「接口说明」可展开查看对应参数、状态码和取舍。

### 创建订单

实际商品 ID 可能不同，以下仅用 ID 5 示例：

```http
POST /orders
Content-Type: application/json
X-Request-ID: demo-create-001
```

```json
{"product_id":5,"quantity":2}
```

成功响应中的关键字段：

```json
{"product_id":5,"quantity":2,"total_amount_cents":5980,"status":"pending"}
```

真实响应还含 `id`（服务生成的 UUID）、`created_at` 和 `updated_at`。响应头 `X-Request-ID` 关联请求日志。前端只预估金额，不传总金额给服务，避免客户端决定价格。

### 模拟支付回调

`order_id` 必须替换成实际返回的 UUID：

```json
{"event_id":"demo-pay-001","order_id":"替换为实际订单UUID","amount_cents":5980}
```

订单 API 接收整数分。页面按元填写，通过平台提交时 `amount_cents` 使用字符串 `"5980"`；平台把它转换成 Python 整数再发给订单 API，避免 JavaScript 大整数舍入。响应摘要中的分也使用字符串；请求记录中的 `body_text` 保存原始业务 JSON，金额仍是订单 API 实际收到的整数。

首次成功返回 `duplicate=false`；同一事件、同一订单和金额重放返回 `duplicate=true`。失败的金额校验不会登记事件，可以修正金额后重新使用该 ID。不同事件通知一个已支付订单会登记事件，但不会再次改变订单状态。这里没有真实支付或外部扣款。

### 取消订单

```http
POST /orders/{实际订单UUID}/cancel
```

无请求体。`pending → cancelled` 并恢复原数量库存；`cancelled → cancelled` 不再恢复；已支付订单返回 `409 / order_already_paid`。取消业务订单与测试工作台的取消测试任务是两种独立操作。

## 数据表和商品管理接口

这些平台接口与上面的订单业务接口用途不同：

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/data/orders` | 读取商品、订单、支付事件及计数的同一 PostgreSQL 只读快照；默认每表加载最多 50 条 |
| `GET /api/data/platform` | 查看测试任务、执行尝试和 Worker；不是业务订单数据 |
| `GET /api/data/products/{id}` | 读取商品当前值及可编辑能力 |
| `POST /api/data/products` | 新增演示商品：名称、字符串分价格、非负整数库存 |
| `PATCH /api/data/products/{id}` | 按读取时原价／原库存条件更新；冲突返回 409，防止覆盖同时发生的库存变化 |
| `DELETE /api/data/products/{id}` | 软删除：停止新订单，保留价格、库存和历史订单 |
| `POST /api/data/products/{id}/restore` | 恢复同一商品，保留当前价格及库存 |

商品管理的完整请求示例见 [商品管理说明](product-management.md)。订单及支付事件通过业务接口维护，数据表不直接编辑这些字段。

## 测试工作台接口速查

这些接口属于自动测试执行平台，不使用手动演示订单。Swagger 已按「平台状态」「测试执行」「报告与证据」「数据浏览与商品管理」「演示订单操作」分组，每个入口都有中文用途说明。

| 方法与路径 | 用途与关键参数 |
| --- | --- |
| `GET /health`（8001） | 平台进程存活；不检查数据库 |
| `GET /health/ready`（8001） | 平台数据库和任务表就绪 |
| `GET /api/suites` | 列出套件 ID、名称、说明和环境要求 |
| `GET /api/suites/{suite_id}/cases` | pytest 用例目录、源码位置和目录版本，用于选择执行范围 |
| `GET /api/system` | Worker 在线数量、租约和队列信息 |
| `GET /api/environment` | 各项实际环境检查及操作提示 |
| `POST /api/runs` | 提交任务：`suite_id`、`timeout_seconds`、可选 `case_ids`／`catalog_revision`；`Idempotency-Key` 头用于去重；新任务 202、重放 200 |
| `GET /api/runs` | 历史列表：`limit`、`offset`、`suite_id`、`status`、`q` |
| `GET /api/runs/{run_id}` | 当前状态、提交范围、尝试、报告和隔离 HTTP 服务 |
| `POST /api/runs/{run_id}/rerun` | 为已结束任务创建关联的新执行，沿用原范围及超时，不覆盖旧报告；未结束返回 409 |
| `POST /api/runs/{run_id}/cancel` | 申请停止 pytest 和清理本次资源；不取消业务订单 |
| `GET /api/runs/{run_id}/log` | pytest 日志，可选 `attempt_id`；最多末尾 256 KiB，明确标记截断 |
| `GET /api/runs/{run_id}/http-services/{service_id}/log` | 该测试任务所属的隔离订单服务日志 |
| `GET /api/runs/{run_id}/evidence` | 已结束任务的证据 JSON；未结束返回 409 |
| `GET /api/runs/{run_id}/junit` | 下载已实际生成的 JUnit XML，可选 `attempt_id` |

任务状态与取消／恢复设计见 [第三阶段说明](stage3.md)，用例参数见 [用例选择](case-selection.md)，报告与历史见 [历史与报告](history-and-reports.md)。

## 如何解释成功、失败与结果未确认

转发接口返回调用记录，**HTTP 状态继续使用真实业务状态**，例如库存不足仍返回 409。记录包括：

- `request`：业务方法、地址、请求 ID、实际请求体文本；
- `response`：业务 HTTP 状态、原始响应文本和响应请求 ID；未收到业务响应时是 `null`；
- `data`：界面使用的响应摘要，金额分为字符串；
- `transport_error`：连接／响应异常，`outcome_unknown` 表示事务结果是否尚未确认；
- `captured_at`、`elapsed_ms`：本次实际时间与耗时，耗时包含就绪检查。

平台参数校验失败时返回 422，尚未调用业务接口。演示能力关闭时返回 403。服务未就绪或数据源不一致时返回 503，业务操作尚未发送。已发送但未收到完整响应时返回 504，不能当作事务回滚；即使服务返回 500／503，也应查询核对。

创建订单还没有幂等键。因此创建结果未确认时，本操作窗口阻止重发；仅库存读回不能确定是否创建过订单，应先查看订单表。支付和取消有业务幂等规则，也先读回，再由用户明确重放；没有自动重试。

操作前后读取是独立 HTTP 查询，展示各自真实读取结果。并发操作时它们不能证明完整的历史库存流水，也不是同一事务中的数据库前后快照。手动请求记录仅保留在本次操作窗口，关闭后不持久保存；正式 pytest 报告仍在测试工作台查看。

## 环境与验证

沿用现有端口：前端 5174、平台 API 8001、演示订单 API 8002、PostgreSQL 5433。两项 API 更新后需重启，没有新增数据库迁移。启动命令：

```bash
.venv/bin/python -m uvicorn probe.main:app --host 127.0.0.1 --port 8002
.venv/bin/python -m uvicorn probe.platform.main:app --host 127.0.0.1 --port 8001
```

两项服务在不同终端运行；前端和 Worker 按现有启动说明运行。`PROBE_PLATFORM_ORDER_API_BASE_URL` 控制演示目标，仅允许本机 127.0.0.1 地址。演示管理开关与商品管理一致：`PROBE_PLATFORM_DEMO_EDIT_ENABLED=false` 或 `APP_ENV=production` 禁止写入。

自动测试始终在独立 `probe_test` 随机 schema 准备数据；平台测试使用 `probe_platform_test` 随机 schema。平台演示入口也会校验上游库名／schema，确保与数据页来源一致。测试工厂只能显式注入受保护的测试 schema。

```bash
.venv/bin/python -m pytest tests/platform/test_demo_orders.py --require-postgres -q
.venv/bin/python -m pytest --require-postgres -q
```

新接口检查包含 HTTP 与实际 PostgreSQL 结果、支付去重、重复取消、金额错误、金额精度、环境隔离、响应丢失后不重试，以及实际 TCP 订单服务的执行与清理。缺环境会报错，不使用 SQLite 替代。

2026-11-10 实际验证：新接口检查 **21 passed**；完整 PostgreSQL 回归 **184 passed、0 failed、0 skipped**，保留 1 条既有 Starlette TestClient 弃用提示。5 组本地浏览器检查覆盖页面下单／取消／支付、重放、错误响应、结果未确认和桌面／手机宽度；浏览器写入使用明确拦截的模拟响应，真实业务行为由上述 PostgreSQL／TCP 检查验证。真实前后演示记录核对一致，另通过正在运行的 8001 → 8002 读取验证相同请求 ID 在两项服务日志中可关联。

记录：`.local/demo-orders-full-regression.log`、`.local/browser-check/demo-order-results.json`；当前没有 GitHub Actions 或正式 UI CI。
