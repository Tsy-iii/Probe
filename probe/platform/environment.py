"""独立探测依赖条件；环境可用不等于用例通过，故障不改变业务与任务数据。"""
import asyncio
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from tempfile import NamedTemporaryFile

import httpx
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from probe.db import Database
from probe.platform.config import ROOT
from probe.platform.models import PlatformBase, TestRun, WorkerRecord
from probe.platform.suites import SUITES


@contextmanager
def readonly_connection(database):
    with database.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            yield connection


def result(status, message, action="", code="", **details):
    return {"status": status, "message": message, "action": action, "code": code, "details": details}


class EnvironmentMonitor:
    def __init__(self, settings, database, *, test_database=None):
        self.settings, self.database = settings, database
        self.owns_test_database = test_database is None
        self.test_database = test_database or Database(settings.order_test_database_url)
        self.platform_test_database = Database(settings.platform_test_database_url)

    def close(self):
        self.platform_test_database.close()
        if self.owns_test_database:
            self.test_database.close()

    def platform_database(self):
        try:
            with readonly_connection(self.database) as connection:
                database, schema = connection.execute(text("SELECT current_database(), current_schema()")).one()
                inspector = inspect(connection)
                names = set(inspector.get_table_names(schema=schema)) if schema else set()
                missing_tables, missing_columns = [], {}
                for table in PlatformBase.metadata.sorted_tables:
                    if table.name not in names:
                        missing_tables.append(table.name)
                        continue
                    found = {column["name"] for column in inspector.get_columns(table.name, schema=schema)}
                    missing = sorted(set(table.columns.keys()) - found)
                    if missing:
                        missing_columns[table.name] = missing
                details = {"database": database, "schema": schema, "missing_tables": missing_tables, "missing_columns": missing_columns}
                if missing_tables or missing_columns:
                    return result("unavailable", "平台表结构尚未就绪，无法保证任务保存与执行。", "激活虚拟环境后运行 python -m probe.platform.manage init-db，再刷新检查。", "schema_mismatch", **details)
                expected = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
                versions = connection.scalars(text("SELECT version_num FROM alembic_version")).all() if "alembic_version" in names else []
                details.update(migration_versions=versions, expected_migration=expected)
                if versions != [expected]:
                    return result("unavailable", "迁移版本与当前代码不一致。", "核对数据库结构与迁移记录，再运行 python -m probe.platform.manage init-db；保留已有数据。", "migration_mismatch", **details)
                if connection.scalar(text("SHOW default_transaction_read_only")) == "on":
                    return result("unavailable", "平台连接默认只读，任务无法正常写入。", "检查 PostgreSQL 服务或连接用户的 default_transaction_read_only 配置。", "default_read_only", **details)
                denied = []
                for table in ["test_runs", "run_attempts", "workers"]:
                    for permission in ["SELECT", "INSERT", "UPDATE"]:
                        relation = f'"{schema}"."{table}"'
                        allowed = connection.scalar(text("SELECT has_table_privilege(current_user, :relation, :permission)"), {"relation": relation, "permission": permission})
                        if not allowed:
                            denied.append(f"{table}: {permission}")
                if denied:
                    return result("unavailable", "连接用户缺少平台表的必要权限。", "为平台连接用户核对表的 SELECT、INSERT、UPDATE 权限，再刷新。", "table_permissions", denied_permissions=denied, **details)
                return result("ok", "平台数据库连接、表结构、迁移版本和必要表权限通过检查。", code="platform_ready", **details)
        except SQLAlchemyError as exc:
            return result("unavailable", "无法读取平台数据库。", "确认 PostgreSQL 已启动，核对 PROBE_PLATFORM_DATABASE_URL 的地址和账号；修复后刷新。", "database_unavailable", error_type=type(exc).__name__)
        except (CommandError, OSError) as exc:
            return result("unknown", "无法读取本项目的迁移定义，结构状态未知。", "核对 migrations 目录与 alembic.ini 是否完整，再刷新。", "migration_definition_unknown", error_type=type(exc).__name__)

    def order_test_database(self):
        try:
            with readonly_connection(self.test_database) as connection:
                database = connection.scalar(text("SELECT current_database()"))
                if database != "probe_test":
                    return result("unavailable", "自动测试必须使用独立的 probe_test 数据库。", "修正 TEST_DATABASE_URL，避免将演示库用于测试。", "not_separate_test_database", database=database)
                allowed = bool(connection.scalar(text("SELECT has_database_privilege(current_user, current_database(), 'CREATE')")))
                default_readonly = connection.scalar(text("SHOW default_transaction_read_only")) == "on"
                details = {"database": database, "can_create_schema": allowed, "default_read_only": default_readonly}
                if default_readonly or not allowed:
                    return result("unavailable", "测试连接不能按当前配置准备临时 schema。", "核对 probe_test 的 CREATE 权限与连接默认只读设置。", "schema_permissions", **details)
                return result("ok", "已连接独立测试库，权限检查允许创建临时 schema。", code="test_database_ready", **details)
        except SQLAlchemyError as exc:
            return result("unavailable", "无法连接独立测试数据库。", "确认 probe_test 已创建，并核对 TEST_DATABASE_URL；无数据库的契约套件不依赖此项。", "test_database_unavailable", error_type=type(exc).__name__)

    def platform_regression_database(self):
        try:
            with readonly_connection(self.platform_test_database) as connection:
                name = connection.scalar(text("SELECT current_database()"))
                allowed = bool(connection.scalar(text("SELECT has_database_privilege(current_user, current_database(), 'CREATE')")))
                readonly = connection.scalar(text("SHOW default_transaction_read_only")) == "on"
                if name != 'probe_platform_test' or not allowed or readonly:
                    return result('unavailable', '演示接口回归需要可创建隔离空间的 probe_platform_test 数据库。', '核对 PLATFORM_TEST_DATABASE_URL、数据库名及 CREATE 权限。', 'platform_test_not_ready', database=name, can_create_schema=allowed)
                return result('ok', '已连接独立平台测试库，可为演示接口回归创建隔离空间。', code='platform_test_ready', database=name, can_create_schema=allowed)
        except SQLAlchemyError as exc:
            return result('unavailable', '无法连接独立平台测试库。', '创建 probe_platform_test 并核对 PLATFORM_TEST_DATABASE_URL；其他套件不依赖此项。', 'platform_test_unavailable', error_type=type(exc).__name__)

    def workers(self):
        try:
            with readonly_connection(self.database) as connection:
                captured = connection.scalar(text("SELECT transaction_timestamp()"))
                online = (WorkerRecord.status == "online") & (WorkerRecord.heartbeat_at > captured - timedelta(seconds=self.settings.worker_lease_seconds))
                count = connection.scalar(select(func.count()).select_from(WorkerRecord).where(online))
                active_count = connection.scalar(select(func.count()).select_from(TestRun).where(TestRun.status.in_(["running", "cancelling", "recovering"])))
                rows = connection.execute(select(WorkerRecord.id, WorkerRecord.process_id, WorkerRecord.status, WorkerRecord.heartbeat_at, online.label("online")).order_by(WorkerRecord.heartbeat_at.desc()).limit(20)).mappings().all()
                details = {"online_workers": count, "active_runs": active_count, "lease_seconds": self.settings.worker_lease_seconds,
                           "workers": [{**row, "id": str(row["id"]), "heartbeat_at": row["heartbeat_at"].isoformat()} for row in rows]}
                if count == 0:
                    return result("waiting", "未检测到近期 Worker 心跳；已接收任务可以在队列中等待。", "另开终端，进入 Probe 并激活虚拟环境，运行 python -m probe.platform.runner。", "no_recent_worker", **details)
                return result("ok", f"检测到 {count} 个近期心跳在线的 Worker。", code="worker_heartbeat_ready", **details)
        except SQLAlchemyError as exc:
            # 读不到心跳并不能说明 Worker 已停止；不能把未知写成离线。
            return result("unknown", "无法读取 Worker 心跳，当前状态未知。", "先恢复平台数据库及表结构，再刷新；不能据此判断 Worker 已停止。", "heartbeat_unknown", error_type=type(exc).__name__)

    def artifacts(self):
        directory = self.settings.artifacts_dir
        try:
            if not directory.is_dir():
                return result("unavailable", "报告目录不存在或配置指向了文件。", "核对 PROBE_PLATFORM_ARTIFACTS_DIR；正常启动 Worker 会创建目录。", "directory_missing", directory=str(directory))
            # 使用随机临时文件验证真实写入／读取能力，关闭时立即删除。
            with NamedTemporaryFile(mode="w+b", prefix=".probe-environment-", dir=directory) as probe:
                probe.write(b"probe environment check"); probe.flush(); probe.seek(0)
                if probe.read() != b"probe environment check":
                    raise OSError("Read-back verification failed")
            return result("ok", "临时文件写入与读取成功，检查文件已删除。", code="artifacts_ready", directory=str(directory))
        except OSError as exc:
            return result("unavailable", "报告目录无法正常写入或读取。", "检查目录配置、当前用户权限和剩余磁盘空间，再刷新。", "artifacts_unwritable", directory=str(directory), error_type=type(exc).__name__)

    def order_api(self):
        url = self.settings.order_api_base_url + "/health/ready"
        try:
            response = httpx.get(url, timeout=2, trust_env=False, follow_redirects=False)
            body = response.json()
            if response.status_code != 200 or not isinstance(body, dict) or body.get("status") != "ready":
                return result("unavailable", "手动订单 API 的健康接口未返回 ready。", "按 README 启动订单 API，并检查其数据库连接。此项不阻断 TestClient 自动测试。", "order_api_not_ready", url=url, http_status=response.status_code)
            return result("ok", "手动演示服务健康接口返回 ready。", code="order_api_ready", url=url)
        except (httpx.HTTPError, ValueError) as exc:
            return result("unavailable", "无法确认手动订单 API 的健康状态。", "运行订单 API 的 Uvicorn 启动命令，核对配置端口。此项不阻断 TestClient 自动测试。", "order_api_unavailable", url=url, error_type=type(exc).__name__)

    def _check(self, identifier, name, operation):
        started = time.perf_counter()
        document = operation()
        return {"id": identifier, "name": name, "checked_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2), **document}

    async def inspect(self):
        definitions = [("platform_database", "平台数据库", self.platform_database), ("test_database", "独立测试数据库", self.order_test_database), ("platform_test_database", "独立平台测试数据库", self.platform_regression_database),
                       ("workers", "Worker 心跳", self.workers), ("artifacts", "报告目录", self.artifacts), ("order_api", "手动订单 API", self.order_api)]
        checks = await asyncio.gather(*(asyncio.to_thread(self._check, *definition) for definition in definitions))
        by_id = {check["id"]: check for check in checks}
        common = [identifier for identifier in ["platform_database", "workers", "artifacts"] if by_id[identifier]["status"] != "ok"]
        suites = [{"id": suite.id, "name": suite.name, "requires_postgres": suite.requires_postgres,
                   "requires_platform_test": suite.requires_platform_test,
                   "blocked_by": common + (["test_database"] if suite.requires_postgres and by_id["test_database"]["status"] != "ok" else []) + (["platform_test_database"] if suite.requires_platform_test and by_id["platform_test_database"]["status"] != "ok" else [])} for suite in SUITES.values()]
        for suite in suites:
            suite["execution_ready"] = not suite["blocked_by"]
        if by_id["platform_database"]["status"] != "ok" or by_id["artifacts"]["status"] != "ok":
            status = "blocked"
        elif by_id["workers"]["status"] != "ok":
            status = "waiting" if by_id["workers"]["status"] == "waiting" else "blocked"
        else:
            status = "ready" if by_id["test_database"]["status"] == by_id["platform_test_database"]["status"] == "ok" else "partial"
        return {"checked_at": datetime.now(timezone.utc).isoformat(), "status": status,
                "can_submit": by_id["platform_database"]["status"] == "ok", "checks": checks, "suites": suites}
