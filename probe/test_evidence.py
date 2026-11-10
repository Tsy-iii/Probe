"""测试主动采集的 HTTP 与数据库证据；在 fixture 清理前保存到 JUnit。"""
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func, select

from probe.models import Order, PaymentEvent, Product


SENSITIVE_KEYS = {"authorization", "cookie", "password", "passwd", "token", "access_token", "refresh_token", "api_key", "apikey", "secret"}
SAFE_HEADERS = {"content-type", "x-request-id", "x-probe-fault"}


def sensitive(key):
    normalized = str(key).lower().replace("-", "_")
    return normalized in SENSITIVE_KEYS or normalized.endswith(("_token", "_secret", "_password"))


def redact(value):
    # 默认不收集认证／Cookie 请求头；JSON 敏感字段也不写入报告。
    if isinstance(value, dict):
        return {key: "[REDACTED]" if sensitive(key) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value[:20]]
    if isinstance(value, str):
        return value[:4000]
    return value


def safe_url(url):
    parsed = urlsplit(str(url))
    query = urlencode([(key, "[REDACTED]" if sensitive(key) else value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)])
    return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, query, ""))


def database_snapshot(database):
    # 这里读取本用例已注入的独立测试 schema，不查询手动演示库。
    # 计数使用 COUNT；行内容最多展示 20 条，避免报告无限增大。
    with database.sessions() as session:
        products = session.scalars(select(Product).order_by(Product.id).limit(20)).all()
        orders = session.scalars(select(Order).order_by(Order.created_at, Order.id).limit(20)).all()
        return {
            "products": [{"id": p.id, "name": p.name, "price_cents": p.price_cents, "stock": p.stock} for p in products],
            "orders": [{"id": str(o.id), "product_id": o.product_id, "quantity": o.quantity, "total_amount_cents": o.total_amount_cents, "status": o.status, "created_at": o.created_at.isoformat(), "updated_at": o.updated_at.isoformat()} for o in orders],
            "order_count": session.scalar(select(func.count()).select_from(Order)),
            "payment_event_count": session.scalar(select(func.count()).select_from(PaymentEvent)),
        }


def check(name, expected, actual):
    return {"name": name, "expected": expected, "actual": actual}


def record_http_evidence(request, response, *, label, before, after, checks, hint=None, related_code=None, transport=None):
    # 必须在断言和 fixture 清理之前调用：断言失败后，证据仍会写入 JUnit。
    try:
        body = json.loads(response.request.content)
    except (ValueError, UnicodeDecodeError):
        body = "[非 JSON 请求正文未收集]"
    try:
        result = response.json()
    except ValueError:
        result = "[非 JSON 响应正文未收集]"
    document = {
        "version": 1,
        "label": label,
        "request": {
            "method": response.request.method,
            "url": safe_url(response.request.url),
            "path": response.request.url.path,
            "headers": {key: value for key, value in response.request.headers.items() if key.lower() in SAFE_HEADERS},
            "body": body,
        },
        "response": {
            "status_code": response.status_code,
            "headers": {key: value for key, value in response.headers.items() if key.lower() in SAFE_HEADERS},
            "body": result,
        },
        "database": {"before": before, "after": after},
        "checks": checks,
        "hint": hint,
        "related_code": related_code,
    }
    if transport is not None:
        document["transport"] = transport
    append_evidence(request, document)


def append_evidence(request, document):
    value = json.dumps(redact(document), ensure_ascii=False, default=str)
    if len(value) > 65_536:
        raise ValueError("Test evidence exceeds 64 KiB")
    # pytest 的 user_properties 会变成 testcase 的 properties；失败也会保留。
    request.node.user_properties.append(("probe.evidence", value))


def record_transport_evidence(request, exception, *, label, before, after, checks, timeout_seconds, elapsed_seconds, hint=None):
    # 没有拿到响应时，状态码必须为 null；不能把连接失败伪装成 HTTP 503。
    outgoing = exception.request
    try:
        body = json.loads(outgoing.content) if outgoing.content else None
    except (ValueError, UnicodeDecodeError):
        body = "[非 JSON 请求正文未收集]"
    kind = type(exception).__name__
    phase = "read" if kind == "ReadTimeout" else "connect" if kind in {"ConnectError", "ConnectTimeout"} else "request"
    append_evidence(request, {
        "version": 1, "label": label,
        "request": {"method": outgoing.method, "url": safe_url(outgoing.url), "path": outgoing.url.path,
                    "headers": {key: value for key, value in outgoing.headers.items() if key.lower() in SAFE_HEADERS}, "body": body},
        "response": {"status_code": None, "headers": {}, "body": None},
        "transport_error": {"type": kind, "phase": phase, "message": "HTTP 请求未取得响应；请按异常类型和服务日志核对。"},
        "transport": {"mode": "network", "timeout_seconds": timeout_seconds, "elapsed_seconds": elapsed_seconds},
        "database": {"before": before, "after": after}, "checks": checks, "hint": hint,
    })
