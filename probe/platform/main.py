import asyncio
import logging
import json
import re
import time
from contextlib import asynccontextmanager
from uuid import UUID, uuid4
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Path, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from probe.db import Database
from probe.config import Settings as OrderSettings
from probe.platform.data_views import order_data, platform_data
from probe.platform.demo_products import create_product, editing_capability, read_product, set_product_active, update_product
from probe.platform.demo_orders import DemoOrderGateway
from probe.platform.case_catalog import catalog, CatalogUnavailable, InvalidCaseSelection
from probe.platform.config import PlatformSettings
from probe.platform.environment import EnvironmentMonitor
from probe.platform.repository import TERMINAL, RunRepository, SubmissionConflict
from probe.platform.runner import SUITES, Worker
from probe.platform.reports import parse_junit
from probe.platform.schemas import DemoExchangeOut, DemoPaymentCallback, DemoProductCreate, DemoProductUpdate, EnvironmentReport, HTTPServiceOut, RerunRun, RunDetail, RunStatus, SubmitResult, SubmitRun, SuiteId
from probe.schemas import CreateOrder
from probe.http_testing import read_services


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("probe.platform")


def create_app(settings: PlatformSettings | None = None, database: Database | None = None, *, worker: Worker | None = None, order_database: Database | None = None, case_catalog=None, environment_monitor=None, demo_transport=None):
    settings = settings or PlatformSettings()
    owns_database = database is None
    database = database or Database(settings.database_url, schema=settings.database_schema)
    repository = RunRepository(database)
    case_catalog = case_catalog or catalog
    owns_monitor = environment_monitor is None
    environment_monitor = environment_monitor or EnvironmentMonitor(settings, database)
    owns_order_database = order_database is None
    order_database = order_database or Database(OrderSettings().database_url)
    demo_gateway = DemoOrderGateway(settings, order_database, injected=not owns_order_database, transport=demo_transport)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if worker:
            await worker.start()  # Tests can embed a real worker; deployment uses the CLI.
        try:
            yield
        finally:
            await demo_gateway.close()
            if worker:
                await worker.close()
            if owns_database:
                database.close()
            if owns_order_database:
                order_database.close()
            if owns_monitor:
                environment_monitor.close()

    app = FastAPI(title="Probe Test Platform", version="0.3.0", lifespan=lifespan)
    app.state.repository, app.state.worker = repository, worker
    app.state.case_catalog = case_catalog

    @app.exception_handler(CatalogUnavailable)
    async def collection_error(request: Request, exc: CatalogUnavailable):
        return JSONResponse(status_code=503, content={"detail": str(exc), "request_id": request.state.request_id})

    @app.exception_handler(InvalidCaseSelection)
    async def selection_error(request: Request, exc: InvalidCaseSelection):
        return JSONResponse(status_code=422, content={"detail": str(exc), "request_id": request.state.request_id})

    @app.middleware("http")
    async def trace(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", supplied) else str(uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info("request_id=%s method=%s path=%s status=%s duration_ms=%.2f", request_id, request.method, request.url.path, response.status_code, (time.perf_counter() - started) * 1000)
        return response

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        logger.error("request_id=%s database_error=%s", request.state.request_id, type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "Platform database operation failed", "request_id": request.state.request_id})

    @app.exception_handler(SubmissionConflict)
    async def state_conflict(request: Request, exc: SubmissionConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc), "request_id": request.state.request_id})

    @app.get("/health", summary="平台进程存活检查", description="确认平台能够响应，不验证数据库或 Worker。", tags=["平台状态"])
    def health():
        return {"status": "ok", "service": "probe-platform"}

    @app.get("/health/ready", summary="平台数据库就绪检查", description="实际连接 PostgreSQL 并检查任务表可读取；失败返回 503。", tags=["平台状态"])
    def ready():
        with database.sessions() as session:
            session.execute(text("SELECT 1 FROM test_runs LIMIT 1"))
        return {"status": "ready", "database": "postgresql"}

    @app.get("/api/suites", summary="列出可执行测试套件", description="返回套件 ID、名称、说明、是否需要 PostgreSQL 及是否预期失败。", tags=["测试执行"])
    def suites():
        return [{"id": s.id, "name": s.name, "description": s.description, "requires_postgres": s.requires_postgres, "expected_failure": s.expected_failure} for s in SUITES.values()]

    @app.get("/api/suites/{suite_id}/cases", summary="读取套件的真实用例目录", description="返回 pytest 收集到的用例、源码位置和目录版本；用于选择全部或部分用例。", tags=["测试执行"])
    def suite_cases(suite_id: str):
        if suite_id not in SUITES:
            raise HTTPException(404, "Test suite does not exist")
        return case_catalog.suite(suite_id)

    @app.get("/api/system", summary="查看 Worker 和队列状态", description="返回最近心跳在线的 Worker 数量、租约和队列信息。", tags=["平台状态"])
    def system():
        return repository.system(settings.worker_lease_seconds)

    @app.get("/api/environment", response_model=EnvironmentReport, summary="检查本地运行环境", description="检查平台／测试数据库、Worker、报告目录和手动订单 API；各项检查独立报告。", tags=["平台状态"])
    async def environment(request: Request):
        # 单项故障仍返回各项实际检查结果，避免一个依赖失败掩盖其他状态。
        document = await environment_monitor.inspect()
        document["request_id"] = request.state.request_id
        return document

    @app.get("/api/data/orders", summary="读取订单演示数据快照", description="同一只读快照返回商品、订单、支付事件和全表计数；limit 控制每表加载上限，focus_product_id 可定位商品。", tags=["数据浏览与商品管理"])
    def demo_data(limit: int = Query(default=50, ge=1, le=100), focus_product_id: int | None = Query(default=None, ge=1, le=2_147_483_647)):
        try:
            document = order_data(order_database, limit, focus_product_id)
            document["product_editing"] = editing_capability(settings, order_database, injected=not owns_order_database)
            document["demo_orders"] = {**document["product_editing"], "base_url": settings.order_api_base_url}
            return document
        except SQLAlchemyError:
            raise HTTPException(503, "Order demo database is unavailable; check database startup and initialization")

    @app.get("/api/data/platform", summary="读取测试执行数据快照", description="返回任务、执行尝试、Worker 及真实计数，供数据页查看；不读取完整报告。", tags=["数据浏览与商品管理"])
    def execution_data(limit: int = Query(default=50, ge=1, le=100)):
        return platform_data(database, settings.worker_lease_seconds, limit)

    @app.get("/api/data/products/{product_id}", summary="读取演示商品当前值", description="返回商品价格、库存、在售状态和编辑能力；包括软删除商品。", tags=["数据浏览与商品管理"])
    def demo_product(product_id: int = Path(ge=1, le=2_147_483_647)):
        return {"product": read_product(order_database, product_id), "product_editing": editing_capability(settings, order_database, injected=not owns_order_database)}

    @app.patch("/api/data/products/{product_id}", summary="修改演示商品价格和库存", description="提交新价格／库存及读取时原值。并发变化返回 409，禁止覆盖其他库存更新。", tags=["数据浏览与商品管理"])
    def edit_demo_product(payload: DemoProductUpdate, request: Request, product_id: int = Path(ge=1, le=2_147_483_647)):
        capability = editing_capability(settings, order_database, injected=not owns_order_database)
        if not capability["enabled"]:
            raise HTTPException(403, capability["reason"])
        try:
            product = update_product(order_database, product_id, payload)
        except SQLAlchemyError as exc:
            logger.error("request_id=%s demo_product_update_error=%s", request.state.request_id, type(exc).__name__)
            raise HTTPException(503, "演示商品保存未确认，请重新读取商品后确认结果")
        logger.info("request_id=%s demo_product_updated=%s price_cents=%s stock=%s", request.state.request_id, product_id, product["price_cents"], product["stock"])
        return {"product": product, "request_id": request.state.request_id}

    def manage_demo_product(operation, request, product_id=None, payload=None):
        capability = editing_capability(settings, order_database, injected=not owns_order_database)
        if not capability["enabled"]:
            raise HTTPException(403, capability["reason"])
        try:
            product = create_product(order_database, payload) if operation == "create" else set_product_active(order_database, product_id, operation == "restore")
        except SQLAlchemyError as exc:
            logger.error("request_id=%s demo_product_%s_error=%s", request.state.request_id, operation, type(exc).__name__)
            raise HTTPException(503, "商品操作结果未确认，请刷新后确认当前状态")
        logger.info("request_id=%s demo_product_operation=%s product_id=%s", request.state.request_id, operation, product["id"])
        return {"product": product, "request_id": request.state.request_id}

    @app.post("/api/data/products", status_code=201, summary="新增演示商品", description="名称 1～100 字符，price_cents 为正整数字符串分，stock 为非负整数。提交成功后返回 201。", tags=["数据浏览与商品管理"])
    def add_demo_product(payload: DemoProductCreate, request: Request):
        return manage_demo_product("create", request, payload=payload)

    @app.delete("/api/data/products/{product_id}", summary="软删除演示商品", description="停止新订单，保留价格、库存、历史订单和支付事件；重复删除不会重复改变库存。", tags=["数据浏览与商品管理"])
    def remove_demo_product(request: Request, product_id: int = Path(ge=1, le=2_147_483_647)):
        return manage_demo_product("remove", request, product_id)

    @app.post("/api/data/products/{product_id}/restore", summary="恢复演示商品", description="恢复同一个商品 ID，保留当前价格和库存；无请求体。", tags=["数据浏览与商品管理"])
    def restore_demo_product(request: Request, product_id: int = Path(ge=1, le=2_147_483_647)):
        return manage_demo_product("restore", request, product_id)

    @app.get("/api/demo/products/{product_id}", response_model=DemoExchangeOut, summary="读取演示商品当前价格和库存", tags=["演示订单操作"])
    async def read_demo_api_product(request: Request, product_id: int = Path(ge=1, le=2_147_483_647)):
        return await demo_gateway.exchange("GET", f"/products/{product_id}", request.state.request_id)

    @app.post("/api/demo/orders", status_code=201, response_model=DemoExchangeOut, summary="通过订单 API 创建演示订单", description="转发到独立订单 API 的 POST /orders；库存扣减和订单写入由订单服务在同一事务内完成。数量为正整数，金额由服务计算。返回真实 HTTP 状态及请求响应记录。", tags=["演示订单操作"])
    async def create_demo_order(payload: CreateOrder, request: Request):
        return await demo_gateway.exchange("POST", "/orders", request.state.request_id, payload.model_dump(mode="json"))

    @app.get("/api/demo/orders/{order_id}", response_model=DemoExchangeOut, summary="查询演示订单当前状态", tags=["演示订单操作"])
    async def query_demo_order(order_id: UUID, request: Request):
        return await demo_gateway.exchange("GET", f"/orders/{order_id}", request.state.request_id)

    @app.post("/api/demo/orders/{order_id}/cancel", response_model=DemoExchangeOut, summary="取消待支付演示订单", description="转发 POST /orders/{order_id}/cancel；pending 取消并恢复库存，重复取消不重复恢复；paid 返回 409。", tags=["演示订单操作"])
    async def cancel_demo_order(order_id: UUID, request: Request):
        return await demo_gateway.exchange("POST", f"/orders/{order_id}/cancel", request.state.request_id)

    @app.post("/api/demo/payments/callback", response_model=DemoExchangeOut, summary="发送模拟支付回调", description="event_id 用于重复事件去重；amount_cents 为整数分的字符串，转发为订单 API 的整数。金额必须匹配订单；取消订单及冲突事件内容返回 409。没有真实扣款。", tags=["演示订单操作"])
    async def pay_demo_order(payload: DemoPaymentCallback, request: Request):
        body = {"event_id": payload.event_id, "order_id": str(payload.order_id), "amount_cents": int(payload.amount_cents)}
        return await demo_gateway.exchange("POST", "/payments/callback", request.state.request_id, body)

    async def accept_submission(suite_id, timeout_seconds, key, case_ids, revision=None, source_id=None):
        # 先处理已接受请求的重放；源码后来变化也不能把旧提交变成新执行。
        run = await asyncio.to_thread(repository.replay, suite_id, timeout_seconds, key, case_ids, source_id)
        if run is None:
            selection = await asyncio.to_thread(case_catalog.selection, suite_id, case_ids, revision)
            run = await asyncio.to_thread(repository.create, suite_id, timeout_seconds, key, settings.max_attempts,
                                          case_ids=case_ids, case_selection=selection, rerun_of_id=source_id)
        return run

    @app.post("/api/runs", response_model=SubmitResult, status_code=202, summary="提交 pytest 测试任务", description="传 suite_id、超时和可选用例 ID／目录版本。Idempotency-Key 用于重复提交去重；新任务返回 202，重放返回 200。", tags=["测试执行"])
    async def submit(payload: SubmitRun, response: Response, request: Request,
                     idempotency_key: str | None = Header(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")):
        run = await accept_submission(payload.suite_id, payload.timeout_seconds, idempotency_key, payload.case_ids, payload.catalog_revision)
        if run.replayed:
            response.status_code = 200
        logger.info("request_id=%s run_id=%s replayed=%s", request.state.request_id, run.id, run.replayed)
        return run

    @app.get("/api/runs", summary="查询与分页执行历史", description="支持 limit、offset、suite_id、status 和 q；返回匹配数量、列表和状态摘要。", tags=["测试执行"])
    def list_runs(limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1_000_000),
                  suite_id: SuiteId | None = None, status: RunStatus | None = None, q: str | None = Query(default=None, max_length=100)):
        return repository.list(limit, offset, suite_id, status, q)

    def require_run(run_id: UUID):
        run = repository.get(run_id)
        if run is None:
            raise HTTPException(404, "Test run does not exist")
        services, warnings = [], []
        for attempt in run.attempts:
            base = settings.artifacts_dir / str(run.id) / str(attempt.id)
            try:
                for item in read_services(base / "http-services.json"):
                    service = HTTPServiceOut.model_validate({**item, "attempt_id": attempt.id})
                    if attempt.cleanup_done and service.status != "stopped":
                        # 完成标记是在真实进程/schema 清理后提交，停止时间未写入时不伪造。
                        service.status, service.cleanup_verified, service.stopped_at = "stopped", True, None
                        service.stop_reason = "attempt_cleanup"
                        run.http_service_notice = "服务清单未保存停止标记；按已完成的尝试清理结果确认停止，具体停止时间未记录。"
                    service.log_available = (base / "http-services" / str(service.id) / "server.log").is_file()
                    services.append(service)
            except (OSError, ValueError, TypeError):
                warnings.append(str(attempt.id))
        run.http_services = services
        if warnings:
            run.http_service_notice = "部分 HTTP 服务信息无法读取，请查看执行日志；不会据此伪造服务状态。"
        return run

    @app.get("/api/runs/{run_id}", response_model=RunDetail, summary="读取任务状态与报告", description="返回执行范围、状态、尝试、请求证据和测试 HTTP 服务信息；不存在返回 404。", tags=["测试执行"])
    def detail(run_id: UUID):
        return require_run(run_id)

    @app.post("/api/runs/{run_id}/rerun", response_model=SubmitResult, status_code=202, summary="基于已结束任务创建新执行", description="沿用原套件、用例选择和超时，保留来源关系；不覆盖旧报告。未结束任务返回 409。", tags=["测试执行"])
    async def rerun(run_id: UUID, response: Response, request: Request, payload: RerunRun | None = None,
                    idempotency_key: str | None = Header(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")):
        source = require_run(run_id)
        if source.status not in TERMINAL:
            raise SubmissionConflict("Only a finished run can be executed again")
        # 有历史选择时复用精确 ID；旧任务没有选择记录，则运行当前套件全部。
        selected = None if source.case_selection is None else [case["id"] for case in source.case_selection["cases"]]
        run = await accept_submission(source.suite_id, source.timeout_seconds, idempotency_key, selected, source_id=source.id)
        if run.replayed:
            response.status_code = 200
        logger.info("request_id=%s run_id=%s source_run_id=%s replayed=%s", request.state.request_id, run.id, source.id, run.replayed)
        return run

    @app.post("/api/runs/{run_id}/cancel", response_model=RunDetail, summary="申请取消测试任务", description="Worker 停止 pytest 并清理本次进程和隔离数据；这不会取消任何业务订单。", tags=["测试执行"])
    def cancel(run_id: UUID):
        run = repository.cancel(run_id)
        if run is None:
            raise HTTPException(404, "Test run does not exist")
        return require_run(run.id)

    def artifact_directory(run, attempt_id):
        chosen = attempt_id or run.active_attempt_id or run.report_attempt_id
        if chosen is None and run.attempts:
            chosen = run.attempts[-1].id
        if chosen is not None and not any(attempt.id == chosen for attempt in run.attempts):
            raise HTTPException(404, "Execution attempt does not belong to this run")
        base = settings.artifacts_dir / str(run.id)
        return base / str(chosen) if chosen is not None else base

    def read_log_path(path):
        if not path.exists():
            return {"content": "", "truncated": False}
        size, limit = path.stat().st_size, 256 * 1024
        with path.open("rb") as source:
            source.seek(max(size - limit, 0))
            content = source.read(limit).decode("utf-8", errors="replace")
        return {"content": content, "truncated": size > limit}

    def read_log(run, attempt_id=None):
        return read_log_path(artifact_directory(run, attempt_id) / "output.log")

    @app.get("/api/runs/{run_id}/log", summary="读取 pytest 执行日志", description="可指定 attempt_id；返回最多末尾 256 KiB 及是否截断，不修改任务。", tags=["报告与证据"])
    def log(run_id: UUID, attempt_id: UUID | None = None):
        return read_log(require_run(run_id), attempt_id)

    @app.get("/api/runs/{run_id}/http-services/{service_id}/log", summary="读取隔离订单测试服务日志", description="服务必须属于该任务；用于关联测试请求和服务错误。", tags=["报告与证据"])
    def http_service_log(run_id: UUID, service_id: UUID):
        run = require_run(run_id)
        service = next((service for service in run.http_services if service.id == service_id), None)
        if service is None:
            raise HTTPException(404, "HTTP test service does not belong to this run")
        path = settings.artifacts_dir / str(run.id) / str(service.attempt_id) / "http-services" / str(service.id) / "server.log"
        return read_log_path(path)

    @app.get("/api/runs/{run_id}/evidence", summary="下载任务证据 JSON", description="仅已结束任务可导出；包含执行范围、报告、请求证据和日志。未结束返回 409。", tags=["报告与证据"])
    def evidence(run_id: UUID):
        run = require_run(run_id)
        if run.status not in TERMINAL:
            raise HTTPException(409, "Evidence can be exported after the execution finishes")
        document = {"schema_version": 1, "exported_at": datetime.now(timezone.utc).isoformat(),
                    "run": run.model_dump(mode="json", exclude={"idempotency_key"}), "log": read_log(run)}
        return Response(content=json.dumps(document, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="probe-{run_id}-evidence.json"'})

    @app.get("/api/runs/{run_id}/junit", summary="下载 pytest JUnit XML", description="可指定执行尝试；只有已生成的文件可下载，不虚构报告。", tags=["报告与证据"])
    def junit(run_id: UUID, attempt_id: UUID | None = None):
        run = require_run(run_id)
        path = artifact_directory(run, attempt_id) / "junit.xml"
        finished = run.status in {"passed", "failed", "error", "timed_out", "cancelled"}
        if attempt_id:
            finished = next(a for a in run.attempts if a.id == attempt_id).finished_at is not None
        if not finished or not path.is_file():
            raise HTTPException(409, "JUnit report is not available yet")
        try:
            parse_junit(path)
        except Exception:
            raise HTTPException(409, "JUnit report is incomplete or invalid")
        return FileResponse(path, media_type="application/xml", filename=f"probe-{run_id}.xml")

    return app


app = create_app()
