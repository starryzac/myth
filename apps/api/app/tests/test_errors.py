import logging
from uuid import UUID

import pytest
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field


def test_missing_resource_has_a_traceable_error() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_invalid_body_does_not_echo_sensitive_input() -> None:
    app = create_app()

    class Body(BaseModel):
        amount_cents: int = Field(gt=0)

    @app.post("/test/body")
    def body_endpoint(body: Body) -> Body:
        return body

    with TestClient(app) as client:
        response = client.post("/test/body", json={"amount_cents": "PRIVATE-ACCOUNT-12345"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "PRIVATE-ACCOUNT" not in response.text


def test_internal_error_is_traceable_without_exposing_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app()

    @app.get("/test/fault")
    def faulty_endpoint() -> None:
        raise RuntimeError("PRIVATE-ACCOUNT-12345")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test/fault")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert str(UUID(response.json()["error"]["request_id"])) == response.headers["x-request-id"]
    assert "PRIVATE-ACCOUNT" not in response.text
    assert "PRIVATE-ACCOUNT" not in caplog.text


def test_request_log_contains_correlation_but_not_query(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="bounded_funds.http")
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health?account=PRIVATE-ACCOUNT-12345")
    messages = " ".join(
        record.getMessage() for record in caplog.records if record.name == "bounded_funds.http"
    )
    assert response.headers["x-request-id"] in messages
    assert "PRIVATE-ACCOUNT" not in messages
