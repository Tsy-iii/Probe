# 用例目录与选择执行

> 当前入口见 [主 README](../README.md)；文末旧验收数字和截图为有日期的历史记录，不代表当前服务状态。`.local/` 与 `test-results/` 仅为运行产物位置，历史原始文件不随仓库发布。

入口：http://127.0.0.1:5174。当前运行 Python 测试文件里的断言，页面提供用例查看与选择，不在线编辑代码。

## 可以直接体验的三个步骤

1. 选择「订单 API 完整测试」，点击左侧的「执行范围 / 查看 / 选择」。目录会显示 pytest 实际收集到的用例，包括每个参数化实例。当前收集结果为 20 条。点击用例名称读说明，勾选框决定是否执行。
2. 点击「清空」，只勾选 `test_create_query_and_pay`，点击「应用选择 · 1 条」。回到工作台应显示「已选 1 条」。超时设为 60，提交新任务；正常结果应为 1 条通过。点击结果区「执行详情」，在侧边抽屉的「本次执行范围」核对具体 ID，再看报告。
3. 选择「接口契约测试」，清空后勾选 `test_quantity_is_a_positive_integer[0]` 和 `test_quantity_is_a_positive_integer[-1]`。应用并提交，正常结果应只执行这两条；再全选提交，应执行当前目录中的全部 6 条。

每次点击「提交测试任务」创建新任务。「重放上次提交」复用上次的套件、超时、用例选择与提交键，返回原任务，不重新测试。

想进一步观察选择的影响，可以选择「金额计算故障对照」，仅勾选 `test_amount_with_correct_implementation`：这次应为 1 条通过。仅选择故障实现时，应为 1 条失败；两条都选时，应为 1 通过、1 失败。套件上的「含故障」说明它包含故意失败的演示用例，不能据此预填本次任务的结果。

## 页面行为

- 选择窗口展示业务标题、真实函数名、参数、源码位置、测试说明和预期结果；PG 标识表示该用例需要真实独立测试数据库。
- 业务说明由开发者编写；用例 ID、参数、数量和位置由 pytest 收集，结果由实际执行决定。尚未提供说明的新用例会提示按源码阅读，不自动猜测业务。
- 「全选」选中当前全部用例，「清空」取消勾选；没有选择时不能应用。
- 点击「返回」、关闭窗口或按 Esc 不改变原选择，点击「应用选择」才保存到工作台。
- 同一标签页保存最近选中的套件及各套件勾选范围，刷新后恢复；旧标签页没有这些缓存时，从上一次提交恢复。缓存中的目录版本仍由后端校验，修改测试文件后需要重新确认选择。套件已不存在时回退到可用套件，存储受限时仍可在当前页面选择。
- 用例列表在独立窗口滚动，套件说明继续在具体套件上悬浮展示，不增加执行历史区域的内容高度。
- 新任务详情显示提交时保存的选择；旧任务标明未保存具体用例选择，实际执行结果仍可从旧报告查看。

## 接口与数据流

`GET /api/suites/{suite_id}/cases` 返回：

```json
{
  "suite_id": "contract",
  "catalog_revision": "目录内容指纹",
  "collected_at": "实际收集时间",
  "count": 6,
  "cases": [
    {
      "id": "tests/test_contract.py::test_quantity_is_a_positive_integer[0]",
      "name": "test_quantity_is_a_positive_integer[0]",
      "title": "数量参数校验",
      "parameters": {"quantity": "0"},
      "requires_postgres": false
    }
  ]
}
```

上面仅摘录一条用例用于说明字段，实际列表包含当前收集到的全部用例。

`POST /api/runs` 选择一条：

```json
{
  "suite_id": "contract",
  "timeout_seconds": 60,
  "case_ids": ["tests/test_contract.py::test_quantity_is_a_positive_integer[0]"]
}
```

省略 `case_ids` 或传 `null` 表示选择提交时的全部用例。传入空数组、重复 ID、不属于该套件的用例或不存在的 ID 会返回 422，且不写任务。浏览器同时提交 `catalog_revision`，如果目录已变化，新提交会被拒绝并提示重新读取。

编辑测试文件后，即使用例 ID 没变，目录指纹也可能变化。`Case list changed` 表示提交前的版本校验拒绝，还没有执行 pytest。点击左侧「查看 / 选择」或提交错误浮层中的「重新选择用例」，重新读取目录、确认原勾选项并点击「应用选择」，再明确提交。页面区分提交错误与平台状态读取错误，后台刷新不会把未处理的提交错误清掉；版本失效不会被误报为平台 API 未启动，也不会自动重试或扩大执行范围。

该恢复流程的前端构建和 5 项浏览器检查通过。验证使用模拟过期目录触发真实 API 的 HTTP 422，重新读取后确认用例 ID 保留、目录版本更新；恢复后的提交回复及另一个 HTTP 409 场景采用浏览器模拟，没有运行用户正在修改的测试。验证前后 35 个任务、演示数据和用户测试源码保持一致，记录在 `.local/browser-check/case-change-recovery-results.json`。

刷新恢复另有 7 项浏览器检查通过，覆盖两个选择入口、单条用例范围保留、旧标签页恢复、失效套件、损坏缓存及存储受限场景；没有提交新任务。记录在 `.local/browser-check/selection-restore-results.json`。

```mermaid
flowchart LR
  Suite[套件白名单] --> Collect[pytest collect-only]
  Collect --> UI[目录与选择窗口]
  UI --> Validate[API 校验选择]
  Validate --> Snapshot[(任务保存 case_selection)]
  Snapshot --> Worker[Worker 使用精确 node ID]
  Worker --> Pytest[pytest 实际执行]
  Pytest --> JUnit[JUnit 含 case_id]
  JUnit --> Report[报告与提交范围对应]
```

## 为什么这样实现

`probe/platform/suites.py` 保留可信套件白名单。目录只收集这些文件或函数，接口不接受任意路径或命令。

`probe/platform/collection.py` 是一个小型 pytest 插件。目录收集时导出 node ID、参数、位置和数据库标识；执行时把同一个 node ID 写入 JUnit 的 `probe.case_id` 属性，报告解析后可以准确对应选择。

`probe/platform/case_catalog.py` 在独立进程执行 `--collect-only`，不会执行测试函数或 fixture 的准备／清理。pytest 仍会导入可信测试模块，因此模块顶层不应写业务副作用。目录按套件缓存，用例、fixture 或收集配置变化后重新读取；一个套件读取失败不会让其他套件收集相同坏文件。读取失败返回 503，而不会伪造空目录。

`test_runs.case_selection` 是新增的可空 JSONB 字段，保存模式、目录指纹、收集时间和所选用例信息。选择全部时也保存精确 ID，排队后新增的用例不会混入旧任务。旧任务保持 NULL，避免补造历史。

`probe/platform/repository.py` 将显式选择纳入提交指纹，按 ID 排序使选择顺序不影响重放；同一提交键携带不同选择返回 409。已接受请求优先返回原任务，不依赖当前目录仍可读取。原来的整套提交保留原指纹格式，旧提交键可继续重放。

`probe/platform/runner.py` 使用保存的 ID 构造 pytest 参数，继续沿用原有进程、目录、数据库 schema 隔离以及超时／取消／恢复机制。保存的用例后来不存在时，任务报告执行异常，不回退成整个套件执行，也不自动把失败重试成通过。

这里保存用例选择和阅读说明，**没有冻结测试源码**。执行使用当时磁盘上的代码；如果以后需要精确重现某次代码版本，可以再引入源码版本或独立 checkout，当前不宣称已具备这个能力。

`frontend/src/CasePicker.tsx` 负责真实目录展示与暂存选择；`App.tsx` 将应用后的选择用于提交，并显示任务保存的范围。报告中的通过数量始终来自实际 JUnit，不使用目录数量替代执行结果。

## 初始化与迁移

从 Probe 根目录、激活虚拟环境后执行：

```bash
python -m probe.platform.manage init-db
python -m alembic current
python -m alembic check
```

当前迁移链为 `0001_stage2 → 0002_reliable_runs → 0003_case_selection → 0004_rerun_source`。第三个迁移增加用例选择，第四个增加重新执行来源，保留已有订单、任务、报告和尝试。API 与 Worker 需要一起更新到支持选择的版本；首次更新时先让活动任务结束，再停止旧版本并迁移，避免旧 Worker 忽略选择。

当前本机服务已更新，可以直接使用 5174；启动命令继续见 [第三阶段说明](stage3.md)。

## 验证与边界

用例选择集成测试：

```bash
python -m pytest tests/platform/test_case_selection.py --require-postgres -q
```

完整回归：

```bash
python -m pytest --require-postgres -q
```

验证包括真实收集和参数化实例、单条／部分／全部执行、精确报告 ID、PostgreSQL 中保存的选择、拒绝非法选择、过期目录、选择变更后的提交冲突、目录失效后的旧请求重放、旧任务兼容，以及已选函数不存在时不扩大执行范围。

2026-11-09 本机验证还包括：前端 TypeScript 与生产构建；浏览器实际提交 1 条订单用例、2 条参数校验、全部 6 条契约用例及独立正确金额用例；检查重放未新增任务、手机窗口与键盘操作、目录读取错误、套件悬浮说明和历史高度。浏览器验收文件在 `.local/browser-check/`，仍未接入正式 UI 测试或 CI。

最终实际回归结果：`73 passed`，`0 failed`，`0 skipped`，其中新增用例选择集成测试 18 条。1 条 Starlette TestClient 弃用提示。`npm run build` 通过，`alembic current` 为 `0003_case_selection (head)`，`alembic check` 未发现新的升级操作。

更新前的 24 个任务详情、报告和尝试已逐条保存并核对；演示订单数据保持不变。验证使用独立测试数据库，测试 schema 在结束后清理。
