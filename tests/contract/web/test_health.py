from pathlib import Path

from fastapi.testclient import TestClient

from pptlib.config import load_settings
from pptlib.web.app import create_app


def test_health_uses_success_envelope(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    client = TestClient(create_app(settings))

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["status"] == "ok"
    assert payload["data"]["version"] == "0.1.0"
    assert payload["request_id"].startswith("req_")


def test_unknown_api_route_uses_error_envelope(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    client = TestClient(create_app(settings))

    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "NOT_FOUND"
    assert payload["error"]["retryable"] is False
    assert payload["request_id"].startswith("req_")


def test_unexpected_exception_uses_internal_error_envelope(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    app = create_app(settings)

    @app.get("/api/v1/test-boom")
    def test_boom() -> None:
        raise RuntimeError("database password=super-secret")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/v1/test-boom")

    assert response.status_code == 500
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert payload["error"]["message"] == "internal server error"
    assert payload["error"]["retryable"] is False
    assert payload["error"]["details"] == {}
    assert "super-secret" not in response.text
    assert payload["request_id"].startswith("req_")
