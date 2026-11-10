# 订单业务设计

> 当前入口见 [主 README](../README.md)；文末旧验收数字和截图为有日期的历史记录，不代表当前服务状态。`.local/` 与 `test-results/` 仅为运行产物位置，历史原始文件不随仓库发布。

本文说明作为测试对象的单商品订单服务。HTTP 接收请求，Pydantic 验证结构，业务函数使用同一 SQLAlchemy Session 操作 PostgreSQL；事务成功提交后才返回成功。每个进程都可运行这些规则，正确性依赖数据库，而非 Python 全局锁。

## 模块各自解决的问题

| 模块 | 问题与选择 | 验证方法 | 边界与取舍 |
|---|---|---|---|
| schemas | 金额用分、整数严格校验，拒绝额外字段；避免浮点与隐式类型转换 | 单字段修改的 422 测试 | UUID 路径由 FastAPI 校验；数量与金额有数据库整数上限 |
| models／db | 持久化商品、订单、事件；约束正数、库存非负、合法状态与引用 | 集成测试查询关键表 | CHECK 约束防非法值，但不能单独保证状态转换；Session 按请求使用 |
| main | HTTP 与事务边界、明确错误响应、request ID | HTTP 与日志断言、实际 curl | 业务错误映射 404／409；SQLAlchemy 错误统一 503，暂未细分超时与约束错误 |
| create_order | 扣库存与订单写入一起完成 | 8 个请求争抢 3 件库存；INSERT 故障触发器 | 未实现创建请求幂等键，超时后重发可能新建第二个订单 |
| pay_order | 事件去重、金额校验与状态转换 | 重放、冲突内容、竞争事件与支付取消竞争 | 只有成功回调被记录；无真实支付或外部扣款 |
| cancel_order | pending 取消并只恢复一次 | 顺序和并发重复取消、已支付取消 | 没有退款、库存补货接口或自动过期取消 |
| config／故障中间件 | 可控延迟与固定错误、默认关闭 | 显式开关、重复错误、延迟下限与库存断言 | 仅供本地开发／测试；故障发生在业务前，不能代替事务中故障 |
| manage | 显式初始化、additive SQL 迁移、幂等 seed 和受保护重置 | 初始化与重复调用检查 | 不自动升级任意结构；平台表独立使用 Alembic |
| tests | 独立测试库、真实提交与清理 | 每例 UUID schema，finally DROP SCHEMA | 本地测试角色有建 schema 权限；中断进程可能留下 case_ schema |

## 创建：原子库存更新 + 事务

关键 SQL 的含义：

```sql
UPDATE products
SET stock = stock - :quantity
WHERE id = :product_id AND stock >= :quantity
RETURNING id, price_cents;
-- 同一事务：INSERT INTO orders (..., status) VALUES (..., 'pending');
```

PostgreSQL 默认 READ COMMITTED 隔离级别下，UPDATE 锁住商品行。竞争请求等待后，数据库依据最新行重新检查库存条件；库存不够的请求不更新任何行。数据库中没有负库存，成功订单的数量总和不超过原始库存。

仅用原子 UPDATE 仍不够：如果后面的订单 INSERT 失败，独立提交的 UPDATE 会让库存凭空减少。因此 `with database.sessions.begin()` 必须涵盖两步，异常退出自动 rollback，正常退出 commit。Pydantic 响应在事务内构建，但 HTTP 成功响应在退出事务后才返回，避免提交失败仍报 201。

并发测试验证竞争结果；`reject_order` 触发器测试验证 UPDATE 已执行后 INSERT 失败的回滚。触发器仅在当前测试 schema 内创建，并随 schema 一起清理。

订单金额是创建时的价格快照，之后商品价格变更也不影响支付金额。整数分避免二进制浮点误差；乘法先用 Python 整数计算，再检查 PostgreSQL bigint 上限，越界同样回滚扣库存。

## 支付：唯一事件 ID + 同一事务

支付过程：检查订单存在 → `INSERT payment_events ON CONFLICT DO NOTHING RETURNING event_id` → 锁定并刷新订单 → 验证金额与状态 → 更新为 paid → 一起提交。

相同 event_id 的并发 INSERT 会由数据库唯一约束协调。后到请求等待前一个事务结束：如果前者成功提交，后者读取已存事件，比较 order_id 与 amount_cents；内容一致返回 duplicate=true，内容不同返回 409 event_conflict。如果前者回滚，后者可以正常登记事件。

金额错误和已取消状态都抛出业务异常，订单与事件一起回滚。失败事件没有登记，所以调用者可以修正金额后重用该 ID。当前仅支持成功支付结果，不模拟失败支付事件审计。

paid 订单收到另一条金额正确的新事件时，新事件被记录，但不再改变订单状态与更新时间。`duplicate` 特指同一事件 ID 重放，不表示“该订单以前是否支付”。已取消订单永远不能被合法回调重新变为 paid。

这里验证的是数据库内业务变更幂等，没有宣称外部支付 exactly-once。未来若发送消息或调用其他服务，应讨论 outbox、消息重复消费和调用方幂等键。

## 为什么使用 FOR NO KEY UPDATE

取消与支付都先锁订单，再决定状态；选用 PostgreSQL `FOR NO KEY UPDATE`（SQLAlchemy `with_for_update(key_share=True)`，实际 SQL 已核验），因为我们不会更改订单主键。

它与其他 NO KEY UPDATE 互斥，所以支付／取消不能同时修改状态；但兼容外键检查取得的 KEY SHARE 锁。支付事件引用订单，INSERT 会检查外键并持有 KEY SHARE；若两个不同事件都插入后再升级成更强的 FOR UPDATE，可能因互持 KEY SHARE 形成锁升级死锁。NO KEY UPDATE 足以保护状态，并避免这类冲突。

支付前的存在性查询会把订单放入 ORM identity map。等待锁以后必须用 `populate_existing=True` 重新加载，否则 ORM 可能仍使用锁前的 pending 状态，错过已完成的取消。支付／取消竞争测试允许任一方先获锁，但最终订单、库存和事件数量必须符合获胜状态。

## 取消：订单锁 + 一次性库存恢复

读取已锁订单：pending 才执行库存 `stock + quantity` 和状态 cancelled，两步同一事务。重复取消直接返回现状；paid 返回 409。第二个取消请求等待第一个提交后读取 cancelled，因此不会重复恢复库存。

创建只锁商品；取消锁订单再更新商品；支付只锁订单与事件约束。没有跨多商品的锁顺序问题。以后若支持多商品订单，需要统一商品锁顺序并重新检查死锁风险。

## 初始化、诊断与当前边界

订单服务启动时不自动建表或 seed。`probe.manage init-db` 创建缺失表并应用商品在售状态的 additive SQL migration；`migrate-db` 只升级演示订单库。订单迁移与平台 Alembic 链分开，不能用平台 `alembic check` 证明订单表没有漂移。

日志含 request_id、method、path、status、实际 duration_ms、故障开关情况；数据库错误额外记录异常类型。请求 ID 只允许有限 ASCII 字符，防止日志换行。日志不记录整个请求体、连接密码或 SQL 参数。无额外重试机制：连接／SQL 超时返回错误并回滚，客户端得到明确结果；测试不会靠重试把失败抹掉。

数据库连接超时 3s，行锁超时 3s，单 SQL 超时 5s，只控制数据库等待；它们不是第二阶段的 pytest 任务超时机制。

第二阶段平台 API 8001 负责提交／执行／报告；订单 API 8002 仍是独立的被测服务。保持独立服务和测试数据库可以让故障注入不破坏平台本身。平台的具体设计见 stage2.md。

## 两个面试展开点

1. **超卖和部分写入是两个不同问题。** 用条件 UPDATE 解决竞争，用事务解决跨表原子性。分别拿并发结果和真实 INSERT 失败后的库存断言作为证据；说明单机测试不能证明所有负载下的性能。
2. **幂等要定义键、内容和失败语义。** 明确同事件同内容、同事件冲突内容、新事件已有 paid 订单、失败回调和并发重放各自结果。唯一约束消除“先查后插”的竞争，事务保证事件登记与状态一致；外部副作用仍需要新的设计。
