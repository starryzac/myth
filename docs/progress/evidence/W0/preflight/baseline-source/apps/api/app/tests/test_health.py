from uuid import UUID

from app.main import app
from fastapi.testclient import TestClient


def test_health_identifies_simulation_api() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "bounded-funds-api", "simulation": True}


def test_each_http_response_has_a_request_id() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert str(UUID(response.headers["x-request-id"])) == response.headers["x-request-id"]
