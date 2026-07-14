"""Tests for CORS middleware and static file config."""

from __future__ import annotations

from fastapi.testclient import TestClient

from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app


def test_cors_not_added_when_no_origins(mock_config: Config) -> None:
    """No CORS headers when cors_origins is empty."""
    app = create_app(mock_config, DaemonLocks(), EventBus())
    client = TestClient(app)
    resp = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    # No CORS allow-origin header.
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_cors_added_when_origins_configured(mock_config: Config) -> None:
    """CORS Allow-Origin returned when the origin is in cors_origins."""
    mock_config.workflow.daemon.cors_origins = ["http://localhost:5173"]
    app = create_app(mock_config, DaemonLocks(), EventBus())
    client = TestClient(app)
    resp = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
