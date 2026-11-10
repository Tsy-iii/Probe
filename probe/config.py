from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://probe:probe_local@127.0.0.1:5433/probe"
    app_env: Literal["development", "test", "production"] = "development"
    fault_injection_enabled: bool = False
    fault_delay_ms: int = Field(default=200, ge=1, le=2000)

    @model_validator(mode="after")
    def protect_production(self):
        if self.app_env == "production" and self.fault_injection_enabled:
            raise ValueError("Fault injection is only allowed in development/test")
        return self
