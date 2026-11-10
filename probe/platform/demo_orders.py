"""固定演示接口转发：调用独立订单服务，不在平台里复制库存或支付业务。"""
import json
import time
from datetime import datetime, timezone

import httpx
from fastapi.responses import JSONResponse

from probe.platform.demo_products import editing_capability


def exact_amounts(value):
    """摘要用字符串传输金额；原始 HTTP JSON 单独保存，避免 JS 舍入 bigint。"""
    if isinstance(value, dict):
        return {key: str(item) if key.endswith("_cents") and isinstance(item, int) else exact_amounts(item) for key, item in value.items()}
    if isinstance(value, list):
        return [exact_amounts(item) for item in value]
    return value


class DemoOrderGateway:
    def __init__(self, settings, database, *, injected=False, transport=None):
        self.settings, self.database, self.injected = settings, database, injected
        # 地址仅来自受限本地配置，页面不能传入任意 URL；不自动跟随重定向或重试。
        self.client = httpx.AsyncClient(
            base_url=settings.order_api_base_url, transport=transport,
            timeout=httpx.Timeout(6, connect=2), trust_env=False, follow_redirects=False,
        )

    async def close(self):
        await self.client.aclose()

    async def exchange(self, method, path, request_id, body=None):
        started = time.perf_counter()
        document = {
            "request_id": request_id, "captured_at": datetime.now(timezone.utc).isoformat(),
            "request": {"method": method, "path": path, "url": self.settings.order_api_base_url + path,
                        "headers": {"X-Request-ID": request_id}, "body_text": None if body is None else json.dumps(body, ensure_ascii=False)},
            "response": None, "data": None, "transport_error": None,
        }

        def finish(status):
            document["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
            return JSONResponse(status_code=status, content=document)

        def unavailable(status, code, message, *, unknown=False):
            document["transport_error"] = {"code": code, "message": message, "outcome_unknown": unknown}
            return finish(status)

        capability = editing_capability(self.settings, self.database, injected=self.injected)
        if not capability["enabled"]:
            return unavailable(403, "demo_disabled", capability["reason"])
        headers = {"X-Request-ID": request_id}
        try:
            ready = await self.client.get("/health/ready", headers=headers)
            identity = ready.json()
            expected_schema = self.database.schema or "public"
            if ready.status_code != 200 or identity.get("status") != "ready":
                return unavailable(503, "order_api_not_ready", "演示订单 API 尚未就绪，请检查 8002 服务和数据库。操作未发送。")
            if (identity.get("database_name") != self.database.engine.url.database
                    or identity.get("schema") != expected_schema or identity.get("app_env") == "production"):
                return unavailable(503, "order_api_target_mismatch", "订单 API 与数据页的数据库或环境不一致。操作未发送，请核对配置并重启订单 API。")
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return unavailable(503, "order_api_unavailable", "无法确认演示订单 API 的状态。操作未发送，请启动订单 API 后重新读取。")

        try:
            options = {"headers": headers}
            if body is not None:
                options["json"] = body
            response = await self.client.request(method, path, **options)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            return unavailable(503, "order_api_connection_failed", "无法连接演示订单 API，请重新读取当前数据。")
        except httpx.HTTPError:
            return unavailable(504, "order_api_response_unconfirmed", "未收到完整响应，操作结果未确认。请查询订单和库存，勿直接重复下单。", unknown=method != "GET")

        document["response"] = {"status_code": response.status_code, "body_text": response.text,
                                "request_id": response.headers.get("X-Request-ID")}
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Expected an object")
            document["data"] = exact_amounts(data)
        except ValueError:
            return unavailable(502, "invalid_order_api_response", "订单 API 返回了无法读取的响应，请查询核对当前状态。", unknown=method != "GET")
        if response.status_code >= 500 or 300 <= response.status_code < 400:
            document["transport_error"] = {"code": "order_api_result_unconfirmed", "message": "订单 API 返回异常，操作结果未确认，请读取核对当前状态。", "outcome_unknown": method != "GET"}
        return finish(response.status_code)
