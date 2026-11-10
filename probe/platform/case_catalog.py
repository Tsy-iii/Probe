"""真实 pytest 用例目录与选择校验；业务说明由开发者编写，不推测断言结果。"""
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from probe.platform.config import ROOT
from probe.platform.suites import SUITES


# 这里只补充阅读说明；用例 ID、参数、数量与源码位置都由 pytest 实际收集。
NOTES = {
    "test_create_query_pay_and_repeat_keep_real_status_and_request_id": ("演示下单、查询与支付", "经平台演示入口创建两件订单，查询、支付并重放同一支付事件。", "201、3980 分、库存 3；订单 paid、事件 1 条，重复通知不重复变更。"),
    "test_cancel_and_repeat_restore_stock_once": ("演示取消与迟到支付", "创建两件订单，取消两次，再发送迟到支付回调。", "取消后库存 5，重复取消不再恢复；迟到支付返回 409，事件数 0。"),
    "test_payment_errors_and_paid_cancel_preserve_database": ("金额与订单状态冲突", "错误金额被拒绝，正确支付后再重用冲突事件并尝试取消。", "错误回调不保留事件；正确支付仅登记一次，已支付取消返回 409。"),
    "test_bad_quantity_changes_only_one_field_and_writes_nothing": ("演示数量校验", "只改变 quantity，传零、负数、小数或布尔值。", "422 定位 quantity，库存 5、订单 0、事件 0。"),
    "test_missing_and_insufficient_stock_return_business_errors": ("演示库存不足与不存在", "请求不存在商品、超库存数量和不存在订单。", "404 / 409 与错误码匹配，库存和订单不改变。"),
    "test_deleted_product_rejects_creation": ("已删除商品禁止下单", "在本用例测试库软删除商品，再创建订单。", "409 product_inactive，库存 5，订单 0。"),
    "test_bigint_amount_is_exact_in_summary_raw_response_and_payment": ("大金额精度", "使用超过 JavaScript 安全整数的单价，下单并支付。", "摘要、原始 JSON 和 PostgreSQL 中金额精确一致。"),
    "test_callback_amount_validation_uses_integer_cents_strings": ("回调金额参数校验", "只改变 amount_cents，检查零、非整数字符串和溢出金额。", "422 定位 amount_cents，不改变订单状态或登记事件。"),
    "test_disabled_demo_never_calls_order_service": ("演示环境写入限制", "关闭演示开关或使用生产环境后请求下单。", "403 拒绝，未调用上游，数据库不变。"),
    "test_target_mismatch_blocks_writes_before_forwarding": ("上游数据来源不一致", "模拟健康接口返回另一份数据库身份。", "503 在转发前阻止下单，库存和订单不变。"),
    "test_lost_reply_after_commit_is_unknown_and_not_retried": ("提交成功但响应丢失", "真实提交订单后，用确定性传输故障丢弃响应。", "平台返回 504 未确认；数据库已扣库存并有 1 条订单，不自动重试。"),
    "test_unreachable_service_does_not_send_mutation": ("演示服务无法连接", "确定性连接异常阻止就绪检查。", "503 标记未发送，库存和订单不变。"),
    "test_request_cannot_choose_an_arbitrary_target": ("固定演示接口边界", "向创建请求添加 url 字段。", "422 拒绝额外字段，不创建订单。"),
    "test_actual_tcp_gateway_executes_order_api_and_cleans_its_service": ("演示接口实际 TCP 链路", "启动本用例拥有的独立订单服务，经平台发真实 TCP 请求。", "创建并支付成功，库存 3、订单 1、事件 1；结束后进程和监听资源清理。"),
    "test_http_create_query_and_pay": ("HTTP 创建、查询与支付", "通过独立服务地址按用例中的数量下单，再查询与支付。", "金额等于单价乘数量，库存扣减对应数量；订单 paid，支付事件一条。"),
    "test_http_cancel_twice_restores_once": ("HTTP 取消与重复取消", "创建 2 件订单，再取消两次。", "库存恢复为 5，重复取消不再次恢复。"),
    "test_http_payment_deduplication_and_conflict": ("HTTP 支付事件幂等", "重复支付事件，再携带不同金额重用事件 ID。", "重复回调返回 duplicate；冲突返回 409，数据库不变。"),
    "test_http_amount_mismatch_leaves_no_event": ("HTTP 支付金额校验", "先发错误金额，核对回滚后再正确支付。", "错误回调不保留事件；正确回调将订单变为 paid。"),
    "test_http_invalid_quantity_changes_one_field": ("HTTP 数量参数校验", "仅修改 quantity 为 0 或 -1。", "422 定位 quantity，库存与订单不变。"),
    "test_http_business_rejection_preserves_database": ("HTTP 库存与商品校验", "请求超出库存的数量，或不存在的商品。", "409 / 404 对应正确错误码，数据库不变。"),
    "test_http_order_state_conflicts": ("HTTP 订单状态冲突", "取消已支付订单，对已取消订单发迟到支付。", "操作被拒绝，库存、订单与事件保持正确。"),
    "test_http_concurrent_creation_cannot_oversell": ("HTTP 并发防超卖", "8 条真实 HTTP 请求竞争 5 件库存。", "5 次成功、3 次拒绝；库存 0、订单 5 条。"),
    "test_http_fixed_error_is_reproducible": ("HTTP 固定错误", "测试服务显式开启故障，发送固定 error 请求头。", "实际 HTTP 503，数据库不变；这是预期行为。"),
    "test_http_read_timeout_has_no_response": ("HTTP 读取超时", "显式延迟 600ms，用 50ms 读取超时请求 GET。", "捕获 ReadTimeout，没有 HTTP 响应；GET 不写订单。"),
    "test_http_connection_failure_has_no_response": ("HTTP 连接阶段故障", "预留一个未监听端口，再发 TCP 请求。", "捕获 ConnectError 或 ConnectTimeout，记录实际类型，没有状态码；该用例不启动订单服务。"),
    "test_http_service_waits_for_execution_control": ("HTTP 服务清理演示", "服务就绪后保持 8 秒供任务控制与清理验证。", "正常完成时通过；取消或超时后服务进程及 schema 清理。"),
    "test_quantity_is_a_positive_integer": ("数量参数校验", "一次只修改数量，分别输入零、负数、小数和布尔值。", "返回 422，并明确定位到 quantity 字段。"),
    "test_health_request_id_and_fault_disabled": ("健康检查与请求 ID", "检查健康响应、请求 ID 与默认关闭的故障开关。", "健康响应正常，日志与响应关联同一个请求 ID。"),
    "test_deterministic_faults_require_explicit_enable": ("显式故障注入", "开启测试故障后，检查固定错误、延迟与非法故障名称。", "固定 503 可重复出现；延迟达到下限；生产环境拒绝故障开关。"),
    "test_create_query_and_pay": ("创建、查询与支付", "创建两件商品的订单，查询记录，再模拟金额匹配的支付。", "金额与库存正确，pending 变为 paid；支付不再次扣库存。"),
    "test_cancel_and_repeat_do_not_restore_twice": ("取消与重复取消", "取消待支付订单，再次取消同一订单。", "状态为 cancelled，库存只恢复一次。"),
    "test_missing_product_and_insufficient_stock_leave_no_order": ("库存不足与商品不存在", "分别请求超出库存的数量和不存在的商品。", "正确拒绝创建，失败后库存与订单保持不变。"),
    "test_missing_order_is_404": ("订单不存在", "用格式合法但不存在的订单 ID 查询、取消或支付。", "返回明确的 404 order_not_found。"),
    "test_amount_mismatch_rolls_back_event_and_can_be_corrected": ("支付金额不匹配与回滚", "发送错误金额，再发送正确金额。", "错误回调不改变订单或保留成功事件；正确支付可成功。"),
    "test_duplicate_and_conflicting_payment_events": ("支付事件去重与内容冲突", "重复同一事件，并用同一事件 ID 携带不同金额或订单。", "重复事件不重复变更；冲突内容返回 409。"),
    "test_cancelled_cannot_be_paid_and_paid_cannot_be_cancelled": ("订单状态冲突", "对已取消订单支付，对已支付订单取消。", "两种操作都被拒绝，订单与库存保持正确。"),
    "test_concurrent_creation_cannot_oversell": ("并发下单防止超卖", "通过不同数据库连接并发竞争有限库存。", "成功购买数量不超过库存，实际订单与剩余库存一致。"),
    "test_concurrent_duplicate_callback_and_cancel": ("并发重复支付与取消", "分别并发发送相同支付事件和相同取消请求。", "支付事件去重，库存不重复恢复。"),
    "test_competing_payment_events_and_payment_cancel_race": ("支付与取消竞争", "不同支付事件竞争同一订单，再让支付与取消同时发生。", "最终状态符合业务规则，库存和事件与最终状态一致。"),
    "test_insert_failure_really_rolls_back_stock": ("数据库写入失败回滚", "在测试数据库内用触发器让订单写入失败。", "已执行的库存扣减被同一事务回滚，无订单残留。"),
    "test_fault_injection_leaves_database_unchanged": ("故障注入后数据不变", "在独立测试库中显式注入固定错误，再检查业务数据。", "返回固定错误，库存、订单与支付事件保持不变。"),
    "test_stock_exactly_enough": ("库存恰好足够", "买完全部 5 件库存，再请求购买 1 件。", "第一次 201、9950 分、库存 0；第二次 409，数据库不变。"),
    "test_amount_with_faulty_implementation": ("金额故障实现对照", "在隔离测试中临时把总金额改为单价，购买 2 件。", "断言期望 3980 分，故障实际为 1990 分；这条用例应失败。"),
    "test_amount_with_correct_implementation": ("金额正确实现回归", "使用正常实现购买 2 件，并核对响应与数据库。", "总金额 3980 分，库存与订单断言通过。"),
    "test_order_creation_under_fixed_error": ("固定错误响应演示", "显式注入 503，但测试断言要求创建成功。", "断言应真实失败，报告保留 201 与 503 的差异。"),
    "test_slow_execution_with_child_process": ("确定性慢执行", "等待 8 秒并创建子进程，用于观察执行控制。", "时间充足时完成；超时或取消时停止并清理。"),
}


class CatalogUnavailable(Exception):
    pass


class InvalidCaseSelection(Exception):
    pass


def belongs_to_suite(case_id, suite):
    return any(case_id == target or case_id.startswith(target + "::") or case_id.startswith(target + "[") for target in suite.targets)


class CaseCatalog:
    def __init__(self):
        self._lock = Lock()
        self._cache = {}

    def _fingerprint(self, files):
        # 用例／fixture／pytest 配置改变时重新收集；请求不启动业务服务或写数据库。
        inputs = {*files, "pytest.ini", "probe/platform/collection.py", "probe/platform/suites.py"}
        if any(file.startswith("tests/") for file in files):
            inputs.add("tests/conftest.py")
        for file in files:
            for parent in Path(file).parents:
                fixture = parent / "conftest.py"
                if (ROOT / fixture).is_file():
                    inputs.add(str(fixture))
        digest = hashlib.sha256()
        for file in sorted(inputs):
            digest.update(file.encode())
            digest.update((ROOT / file).read_bytes())
        return digest.hexdigest()

    def suite(self, suite_id):
        if suite_id not in SUITES:
            raise InvalidCaseSelection("Test suite does not exist")
        suite = SUITES[suite_id]
        files = sorted({target.split("::")[0] for target in suite.targets})
        try:
            with self._lock:
                revision = self._fingerprint(files)
                cached = self._cache.get(suite_id)
                if cached is None or revision != cached["revision"]:
                    with TemporaryDirectory(prefix="probe-catalog-") as temp:
                        output = Path(temp) / "cases.json"
                        result = subprocess.run([
                            sys.executable, "-m", "pytest", *suite.targets, "--collect-only", "-q",
                            "-c", str(ROOT / "pytest.ini"), "--rootdir", str(ROOT), "-o", "addopts=",
                            "-p", "no:cacheprovider", "-p", "probe.platform.collection", "--probe-collection-output", str(output),
                        ], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                        if result.returncode != 0:
                            raise CatalogUnavailable("Could not collect pytest cases; run pytest --collect-only locally to check test imports")
                        cases = json.loads(output.read_text(encoding="utf-8"))
                    # 收集过程中源码变动时拒绝此次快照，避免混用新旧目录。
                    if revision != self._fingerprint(files):
                        raise CatalogUnavailable("Test files changed during collection; refresh the case list")
                    cached = {"cases": cases, "revision": revision, "collected_at": datetime.now(timezone.utc).isoformat()}
                    self._cache[suite_id] = cached
                rows = []
                for case in cached["cases"]:
                    if not belongs_to_suite(case["id"], SUITES[suite_id]):
                        continue
                    title, description, expected = NOTES.get(case["function"], (case["function"], "此用例尚未提供业务说明，请按源码阅读。", "以源码中的实际断言为准。"))
                    rows.append({**case, "title": title, "description": description, "expected": expected})
                if not rows:
                    raise CatalogUnavailable("This suite has no collected cases")
                return {"suite_id": suite_id, "catalog_revision": cached["revision"], "collected_at": cached["collected_at"], "count": len(rows), "cases": rows}
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise CatalogUnavailable("Could not collect pytest cases; check test files and Python dependencies") from exc

    def selection(self, suite_id, case_ids=None, revision=None):
        directory = self.suite(suite_id)
        if revision is not None and revision != directory["catalog_revision"]:
            raise InvalidCaseSelection("Case list changed; refresh and select cases again")
        requested = set(case_ids) if case_ids is not None else {case["id"] for case in directory["cases"]}
        allowed = {case["id"] for case in directory["cases"]}
        if not requested or not requested <= allowed:
            raise InvalidCaseSelection("Selected cases must exist in this suite; refresh the case list")
        return {"mode": "all" if case_ids is None else "selected", "catalog_revision": directory["catalog_revision"], "collected_at": directory["collected_at"], "cases": [case for case in directory["cases"] if case["id"] in requested]}


catalog = CaseCatalog()
