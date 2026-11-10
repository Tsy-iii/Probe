from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from probe.schemas import EventId


SuiteId = Literal["stage1", "contract", "failure_demo", "slow_demo", "isolation", "stock_boundary", "amount_bug_demo", "http_orders", "http_control", "demo_orders"]
RunStatus = Literal["queued", "running", "cancelling", "recovering", "passed", "failed", "error", "timed_out", "cancelled"]


class DemoProductUpdate(BaseModel):
    # 与只读数据接口一致：bigint 分用十进制字符串传输，不能经 JS 浮点数转换。
    model_config = ConfigDict(extra="forbid")
    price_cents: str = Field(strict=True, pattern=r"^[1-9][0-9]{0,18}$")
    stock: int = Field(strict=True, ge=0, le=2_147_483_647)
    expected_price_cents: str = Field(strict=True, pattern=r"^[1-9][0-9]{0,18}$")
    expected_stock: int = Field(strict=True, ge=0, le=2_147_483_647)

    @field_validator("price_cents", "expected_price_cents")
    @classmethod
    def price_fits_postgres(cls, value):
        if int(value) > 9_223_372_036_854_775_807:
            raise ValueError("Price exceeds PostgreSQL bigint range")
        return value


class DemoProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(strict=True, min_length=1, max_length=100)
    price_cents: str = Field(strict=True, pattern=r"^[1-9][0-9]{0,18}$")
    stock: int = Field(strict=True, ge=0, le=2_147_483_647)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Product name cannot be blank")
        return value

    @field_validator("price_cents")
    @classmethod
    def price_fits_postgres(cls, value):
        return DemoProductUpdate.price_fits_postgres(value)


class DemoPaymentCallback(BaseModel):
    """页面提交十进制字符串分；转发时转换为订单 API 要求的整数。"""
    model_config = ConfigDict(extra="forbid")
    event_id: EventId
    order_id: UUID
    amount_cents: str = Field(strict=True, pattern=r"^[1-9][0-9]{0,18}$")

    @field_validator("amount_cents")
    @classmethod
    def amount_fits_postgres(cls, value):
        return DemoProductUpdate.price_fits_postgres(value)


class DemoExchangeOut(BaseModel):
    request_id: str = Field(description="关联平台与订单 API 日志的请求 ID。")
    captured_at: datetime = Field(description="本次操作发起时间。")
    elapsed_ms: float = Field(description="实际耗时，包含就绪检查，不作为性能基准。")
    request: dict = Field(description="被测接口的方法、路径、完整 URL、请求 ID 和原始请求体文本。")
    response: dict | None = Field(description="实际业务 HTTP 状态、原始响应文本和响应请求 ID；未收到业务响应时为 null。")
    data: dict | None = Field(description="供界面显示的响应摘要，金额分转为字符串以保留 bigint 精度。原始 JSON 在 response.body_text。")
    transport_error: dict | None = Field(description="连接／超时等异常；outcome_unknown=true 表示操作是否提交尚未确认。")


class SubmitRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suite_id: SuiteId
    timeout_seconds: int = Field(default=60, ge=1, le=300, strict=True)
    case_ids: list[Annotated[str, Field(strict=True, min_length=1, max_length=500)]] | None = Field(default=None, min_length=1, max_length=500)
    catalog_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("case_ids")
    @classmethod
    def no_duplicate_cases(cls, value):
        if value is not None and len(value) != len(set(value)):
            raise ValueError("Duplicate case IDs are not allowed")
        return value


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    suite_id: str
    case_selection: dict | None = None
    rerun_of_id: UUID | None = None
    status: RunStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    total_count: int | None
    passed_count: int | None
    failed_count: int | None
    error_count: int | None
    skipped_count: int | None
    error_message: str | None
    idempotency_key: str
    timeout_seconds: int
    max_attempts: int
    attempt_count: int
    worker_id: UUID | None
    lease_until: datetime | None
    cancel_requested_at: datetime | None
    active_attempt_id: UUID | None
    report_attempt_id: UUID | None


class AttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    number: int
    worker_id: UUID
    status: str
    started_at: datetime
    finished_at: datetime | None
    process_id: int | None
    isolation_schema: str | None
    stop_reason: str | None
    cleanup_done: bool
    exit_code: int | None


class HTTPServiceOut(BaseModel):
    id: UUID
    attempt_id: UUID
    case_id: str
    base_url: str = Field(pattern=r"^http://127\.0\.0\.1:[0-9]{1,5}$")
    process_id: int | None
    isolation_schema: str = Field(validation_alias=AliasChoices("isolation_schema", "schema"), pattern=r"^(case|run)_[a-f0-9]{32}$")
    status: Literal["starting", "ready", "stopped", "error"]
    started_at: datetime
    ready_at: datetime | None
    stopped_at: datetime | None
    exit_code: int | None
    cleanup_verified: bool
    stop_reason: str | None
    log_available: bool = False


class RunDetail(RunOut):
    report: dict | None
    attempts: list[AttemptOut] = Field(default_factory=list)
    http_services: list[HTTPServiceOut] = Field(default_factory=list)
    http_service_notice: str | None = None


class SubmitResult(RunOut):
    replayed: bool


class RerunRun(BaseModel):
    # 重新执行复用原任务参数，不能通过未声明字段偷偷扩大用例范围。
    model_config = ConfigDict(extra="forbid")


class EnvironmentCheck(BaseModel):
    id: Literal["platform_database", "test_database", "platform_test_database", "workers", "artifacts", "order_api"]
    name: str
    status: Literal["ok", "waiting", "unavailable", "unknown"]
    code: str
    message: str
    action: str
    details: dict
    checked_at: datetime
    duration_ms: float = Field(ge=0)


class EnvironmentSuite(BaseModel):
    id: SuiteId
    name: str
    requires_postgres: bool
    requires_platform_test: bool = False
    execution_ready: bool
    blocked_by: list[str]


class EnvironmentReport(BaseModel):
    checked_at: datetime
    request_id: str
    status: Literal["ready", "partial", "waiting", "blocked"]
    can_submit: bool
    checks: list[EnvironmentCheck]
    suites: list[EnvironmentSuite]
