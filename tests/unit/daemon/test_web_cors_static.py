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


def test_static_serving_when_dist_exists(mock_config: Config, tmp_path) -> None:
    """When frontend_dist exists, the app serves its index.html at /."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>SPA</body></html>", encoding="utf-8")
    mock_config.workflow.daemon.serve_frontend = True
    mock_config.workflow.daemon.frontend_dist = str(dist)

    app = create_app(mock_config, DaemonLocks(), EventBus())
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "SPA" in resp.text


def test_static_serving_skipped_when_dist_missing(mock_config: Config, tmp_path) -> None:
    """When frontend_dist doesn't exist, / is not served (API still works)."""
    mock_config.workflow.daemon.serve_frontend = True
    mock_config.workflow.daemon.frontend_dist = str(tmp_path / "nonexistent")
    app = create_app(mock_config, DaemonLocks(), EventBus())
    client = TestClient(app)
    # API still works.
    assert client.get("/api/health").status_code == 200
    # Root is 404 (no static, no SPA).
    resp = client.get("/")
    assert resp.status_code == 404


def test_spa_fallback_serves_index_for_unknown_paths(mock_config: Config, tmp_path) -> None:
    """Unknown non-/api paths serve index.html (SPA client-side routing)."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>SPA fallback</html>", encoding="utf-8")
    mock_config.workflow.daemon.serve_frontend = True
    mock_config.workflow.daemon.frontend_dist = str(dist)

    app = create_app(mock_config, DaemonLocks(), EventBus())
    client = TestClient(app)
    resp = client.get("/some/spa/route")
    assert resp.status_code == 200
    assert "SPA fallback" in resp.text
