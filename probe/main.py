import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from probe.config import Settings
from probe.db import Database
from probe.schemas import CreateOrder, OrderOut, PaymentCallback, PaymentOut, ProductOut
from probe.service import BusinessError, cancel_order, create_order, get_order, get_product, pay_order


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("probe.requests")


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or Settings()
    owns_database = database is None
    database = database or Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if owns_database:
            database.close()

    app = FastAPI(title="Probe Order API", version="0.1.0", lifespan=lifespan)
    app.state.database = database
    app.state.settings = settings

    def error_response(request: Request, status: int, code: str, message: str):
        return JSONResponse(status_code=status, content={
            "error": {"code": code, "message": message},
            "request_id": request.state.request_id,
        })

    @app.middleware("http")
    async def trace_and_inject(request: Request, call_next):
        supplied_id = request.headers.get("X-Request-ID", "")
        request_id = supplied_id if re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", supplied_id) else str(uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            fault = request.headers.get("X-Probe-Fault")
            if settings.fault_injection_enabled and fault and request.url.path != "/health":
                if fault == "error":
                    response = error_response(request, 503, "injected_error", "Deterministic development error")
                elif fault == "delay":
                    await asyncio.sleep(settings.fault_delay_ms / 1000)
                    response = await call_next(request)
                else:
                    response = error_response(request, 400, "invalid_fault", "Use delay or error")
            else:
                response = await call_next(request)
        except Exception:
            logger.exception("request_id=%s method=%s path=%s unhandled_error", request_id, request.method, request.url.path)
            response = error_response(request, 500, "internal_error", "Unexpected server error")
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_id=%s method=%s path=%s status=%s duration_ms=%.2f fault=%s",
            request_id, request.method, request.url.path, response.status_code,
            (time.perf_counter() - started) * 1000,
            request.headers.get("X-Probe-Fault") if settings.fault_injection_enabled else "disabled",
        )
        return response

    @app.exception_handler(BusinessError)
    async def business_error(request: Request, exc: BusinessError):
        return error_response(request, exc.status, exc.code, exc.message)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        # Don't log connection URLs, SQL parameters or entire exceptions; logs
        # identify the request and error class without exposing local secrets.
        logger.error("request_id=%s database_error=%s", request.state.request_id, type(exc).__name__)
        return error_response(request, 503, "database_error", "Database operation failed")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        body = {
            "error": {"code": "validation_error", "message": "Invalid request parameters"},
            "request_id": request.state.request_id,
            "details": [{"loc": list(e["loc"]), "type": e["type"], "message": e["msg"]} for e in exc.errors()],
        }
        return JSONResponse(status_code=422, content=body)

    @app.get("/health", summary="检查订单 API 进程是否存活", description="只检查应用能否响应，不访问数据库；数据库可用性请使用 /health/ready。")
    def health():
        return {"status": "ok"}

    @app.get("/health/ready", summary="检查 PostgreSQL 就绪与数据来源", description="实际连接 PostgreSQL，返回库名、schema 和运行环境。演示入口用它确认订单 API 与数据页连接同一份数据。数据库不可用时返回 503。")
    def ready():
        with database.sessions() as session:
            name, schema = session.execute(text("SELECT current_database(), current_schema()")).one()
        # 演示入口确认自己连接的是同一业务库，避免配置不同步时操作另一份数据。
        return {"status": "ready", "database": "postgresql", "database_name": name, "schema": schema, "app_env": settings.app_env}

    @app.get("/products/{product_id}", response_model=ProductOut, summary="查询商品价格、库存和在售状态", responses={404: {"description": "商品不存在"}}, description="只读查询，金额为整数分。已软删除商品仍可查询，is_active=false。")
    def product(product_id: int):
        with database.sessions() as session:
            return ProductOut.model_validate(get_product(session, product_id))

    @app.post("/orders", status_code=201, response_model=OrderOut, summary="创建单商品订单并扣减库存", description="数量必须为正整数。金额由当前单价乘数量计算。扣库存与写订单在同一 PostgreSQL 事务中完成，成功提交后返回 pending。此接口尚无创建幂等键，响应超时后先查询核对，不自动重试。", responses={404: {"description": "商品不存在"}, 409: {"description": "库存不足、商品已删除或金额溢出"}})
    def create(payload: CreateOrder):
        with database.sessions.begin() as session:
            result = OrderOut.model_validate(create_order(session, payload))
        return result  # Respond only after commit succeeds.

    @app.get("/orders/{order_id}", response_model=OrderOut, summary="查询订单当前状态和下单金额", description="返回订单 ID、商品 ID、数量、固定的下单总额及创建／更新时间。商品后来改价不会改变订单金额。", responses={404: {"description": "订单不存在"}})
    def query(order_id: UUID):
        with database.sessions() as session:
            return OrderOut.model_validate(get_order(session, order_id))

    @app.post("/orders/{order_id}/cancel", response_model=OrderOut, summary="取消待支付订单并恢复库存", description="没有请求体。pending 变为 cancelled 并恢复数量对应的库存；重复取消返回同一订单，不重复恢复。paid 不能取消。", responses={404: {"description": "订单不存在"}, 409: {"description": "已支付订单不能取消"}})
    def cancel(order_id: UUID):
        with database.sessions.begin() as session:
            result = OrderOut.model_validate(cancel_order(session, order_id))
        return result

    @app.post("/payments/callback", response_model=PaymentOut, summary="处理模拟支付回调与事件去重", description="传稳定 event_id、订单 UUID 和整数分金额。金额必须匹配订单。相同事件及内容重放时 duplicate=true；同一事件携带冲突内容返回 409。已取消订单不能支付。不同事件通知同一已支付订单会登记事件，但不再次改变订单。没有真实扣款。", responses={404: {"description": "订单不存在"}, 409: {"description": "金额不一致、订单已取消或事件 ID 内容冲突"}})
    def callback(payload: PaymentCallback):
        with database.sessions.begin() as session:
            order, duplicate = pay_order(session, payload)
            result = PaymentOut(order=OrderOut.model_validate(order), duplicate=duplicate)
        return result

    return app


app = create_app()
