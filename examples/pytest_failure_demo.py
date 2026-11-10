"""Explicitly selected by the platform; excluded from the normal tests/ suite."""
from fastapi.testclient import TestClient

from probe.config import Settings
from probe.main import create_app


def test_order_creation_under_fixed_error():
    settings = Settings(
        app_env="test", fault_injection_enabled=True,
        database_url="postgresql+psycopg://probe:unused@127.0.0.1:1/probe",
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/orders", json={"product_id": 1, "quantity": 1},
            headers={"X-Probe-Fault": "error", "X-Request-ID": "failure-demo-001"},
        )
    # This deliberately fails: the test expected a successful creation while
    # the explicitly injected deterministic fault returned 503 before DB access.
    assert response.status_code == 201, f"Expected 201; got {response.status_code}: {response.json()}"
