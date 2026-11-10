"""仅用于独立 HTTP 测试的订单服务进程，不建表、不准备业务数据。"""
import argparse
import os
import re
import socket
import threading
from uuid import UUID

import uvicorn
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from probe.config import Settings
from probe.db import Database
from probe.main import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket-fd", type=int, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--service-id", type=UUID, required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--allow-faults", action="store_true")
    parser.add_argument("--delay-ms", type=int, default=600)
    args = parser.parse_args()
    url = os.environ.get("PROBE_HTTP_TEST_DATABASE_URL", "")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+psycopg" or parsed.database != "probe_test":
        raise ValueError("HTTP test service requires the independent probe_test database")
    if not re.fullmatch(r"(?:case|run)_[a-f0-9]{32}", args.schema):
        raise ValueError("HTTP test service requires a validated test schema")
    if os.getppid() != args.parent_pid or os.environ.get("PROBE_HTTP_SERVICE_ID") != str(args.service_id):
        raise ValueError("HTTP test service owner does not match")
    listener = socket.socket(fileno=args.socket_fd)
    if listener.getsockname()[0] != "127.0.0.1":
        raise ValueError("HTTP test service must listen on loopback")
    database = Database(url, schema=args.schema)
    try:
        # 表和演示商品必须由测试 fixture 提前准备，进程不能误用 public 或演示库。
        with database.engine.connect() as connection:
            if connection.scalar(text("SELECT current_schema()")) != args.schema:
                raise ValueError("HTTP test schema is unavailable")
            connection.execute(text("SELECT 1 FROM products LIMIT 1"))
        settings = Settings(database_url=url, app_env="test", fault_injection_enabled=args.allow_faults, fault_delay_ms=args.delay_ms)
        app = create_app(settings, database)
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on", timeout_graceful_shutdown=1))
        finished = threading.Event()

        def watch_parent():
            while not finished.wait(0.1):
                if os.getppid() != args.parent_pid:
                    server.should_exit = True
                    return

        watchdog = threading.Thread(target=watch_parent, daemon=True, name="probe-http-parent-watch")
        watchdog.start()
        try:
            server.run(sockets=[listener])
        finally:
            finished.set(); watchdog.join(timeout=0.5)
    finally:
        listener.close(); database.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, SQLAlchemyError, OSError) as exc:
        # 连接 URL 和 SQL 参数可能含凭据，启动日志仅显示错误类型。
        print(f"HTTP test service startup failed: {type(exc).__name__}", flush=True)
        raise SystemExit(2)
