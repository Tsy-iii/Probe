# 证据采集的 HTTP 契约测试：参数校验在数据库操作前返回，可独立验证脱敏。
import json

from probe.test_evidence import check, record_http_evidence


def test_request_evidence_redacts_credentials(offline_client, request):
    response = offline_client.post(
        "/orders?access_token=test-query-secret",
        json={"product_id": 1, "quantity": 5, "password": "test-body-secret"},
        headers={"Authorization": "Bearer test-header-secret", "Cookie": "session=test-cookie-secret", "X-Request-ID": "evidence-redaction-001"},
    )
    assert response.status_code == 422  # password 是未定义字段，应被输入校验拒绝。
    record_http_evidence(request, response, label="敏感字段不进入报告", before={}, after={}, checks=[check("HTTP 状态码", 422, response.status_code)])
    serialized = request.node.user_properties[-1][1]
    document = json.loads(serialized)
    for secret in ["test-query-secret", "test-body-secret", "test-header-secret", "test-cookie-secret"]:
        assert secret not in serialized
    assert document["request"]["body"]["password"] == "[REDACTED]"
    assert "authorization" not in document["request"]["headers"]
    assert "cookie" not in document["request"]["headers"]
    assert document["response"]["headers"]["x-request-id"] == "evidence-redaction-001"
