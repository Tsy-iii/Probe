from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[2]


class PlatformSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROBE_PLATFORM_", env_file=".env", extra="ignore", populate_by_name=True)

    database_url: str = "postgresql+psycopg://probe:probe_local@127.0.0.1:5433/probe_platform"
    app_env: Literal["development", "test", "production"] = Field(default="development", validation_alias="APP_ENV")
    demo_edit_enabled: bool = True
    order_test_database_url: str = Field(
        default="postgresql+psycopg://probe:probe_local@127.0.0.1:5433/probe_test",
        validation_alias="TEST_DATABASE_URL",
    )
    platform_test_database_url: str = Field(
        default="postgresql+psycopg://probe:probe_local@127.0.0.1:5433/probe_platform_test",
        validation_alias="PLATFORM_TEST_DATABASE_URL",
    )
    artifacts_dir: Path = ROOT / ".local" / "runs"
    order_api_base_url: str = Field(default="http://127.0.0.1:8002", pattern=r"^http://127\.0\.0\.1:[0-9]{1,5}$")
    database_schema: str | None = Field(default=None, pattern=r"^platform_case_[a-f0-9]{32}$")
    worker_lease_seconds: float = Field(default=6, ge=2, le=60)
    heartbeat_seconds: float = Field(default=1, ge=0.1, le=10)
    poll_seconds: float = Field(default=0.2, ge=0.05, le=2)
    max_attempts: int = Field(default=2, ge=1, le=3)

    @model_validator(mode="after")
    def require_separate_databases(self):
        platform = make_url(self.database_url)
        test = make_url(self.order_test_database_url)
        platform_test = make_url(self.platform_test_database_url)
        if platform.drivername != "postgresql+psycopg" or platform.database not in {"probe_platform", "probe_platform_test"}:
            raise ValueError("Platform database must be probe_platform or probe_platform_test on PostgreSQL")
        if test.drivername != "postgresql+psycopg" or test.database != "probe_test":
            raise ValueError("Order test execution requires the independent PostgreSQL probe_test database")
        if platform_test.drivername != "postgresql+psycopg" or platform_test.database != "probe_platform_test":
            raise ValueError("Platform regression execution requires the independent PostgreSQL probe_platform_test database")
        self.artifacts_dir = self.artifacts_dir.resolve()
        if not 1 <= int(self.order_api_base_url.rsplit(":", 1)[1]) <= 65535:
            raise ValueError("Order API port must be between 1 and 65535")
        if self.worker_lease_seconds < self.heartbeat_seconds * 3:
            raise ValueError("Worker lease must be at least three heartbeat intervals")
        return self
