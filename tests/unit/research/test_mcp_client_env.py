"""Tests for proxy/CA-bundle env forwarding in McpClient.

The MCP SDK's ``get_default_environment()`` only whitelists a handful of OS
vars, so proxy and CA-bundle settings would be dropped before reaching a stdio
subprocess. ``_forward_network_env`` re-injects them from ``os.environ``.
"""

from __future__ import annotations

import pytest

from devflow.research.mcp_client import _FORWARDED_NETWORK_ENV_VARS, _forward_network_env

# Every variable we expect to be forwarded.
_ALL_FORWARD_VARS = _FORWARDED_NETWORK_ENV_VARS


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove all forwarded vars from os.environ so tests start from a known state."""
    for var in _ALL_FORWARD_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_forward_proxy_env_set(clean_env: pytest.MonkeyPatch) -> None:
    """HTTP/HTTPS proxy and NO_PROXY (both cases) are forwarded when set."""
    clean_env.setenv("HTTP_PROXY", "http://proxy.corp:8080")
    clean_env.setenv("HTTPS_PROXY", "http://proxy.corp:8080")
    clean_env.setenv("NO_PROXY", "localhost,127.0.0.1,.corp")
    clean_env.setenv("no_proxy", "localhost,127.0.0.1,.corp")

    result = _forward_network_env({"REDMINE_URL": "https://redmine.example.com"})

    assert result["HTTP_PROXY"] == "http://proxy.corp:8080"
    assert result["HTTPS_PROXY"] == "http://proxy.corp:8080"
    assert result["NO_PROXY"] == "localhost,127.0.0.1,.corp"
    assert result["no_proxy"] == "localhost,127.0.0.1,.corp"
    # Original env keys are preserved.
    assert result["REDMINE_URL"] == "https://redmine.example.com"


def test_forward_ssl_cert_env_set(clean_env: pytest.MonkeyPatch) -> None:
    """SSL_CERT_FILE and REQUESTS_CA_BUNDLE are forwarded when set."""
    clean_env.setenv("SSL_CERT_FILE", "/etc/ssl/corp-ca.pem")
    clean_env.setenv("REQUESTS_CA_BUNDLE", "/etc/ssl/corp-ca.pem")

    result = _forward_network_env(None)

    assert result["SSL_CERT_FILE"] == "/etc/ssl/corp-ca.pem"
    assert result["REQUESTS_CA_BUNDLE"] == "/etc/ssl/corp-ca.pem"


def test_forward_no_env_when_unset(clean_env: pytest.MonkeyPatch) -> None:
    """With no network vars set and empty input, no forwarded keys appear."""
    result = _forward_network_env({})

    for var in _ALL_FORWARD_VARS:
        assert var not in result


def test_forward_server_env_takes_precedence(clean_env: pytest.MonkeyPatch) -> None:
    """An explicit value in ``env`` wins over os.environ (setdefault)."""
    clean_env.setenv("HTTPS_PROXY", "http://from-os-env:8080")

    result = _forward_network_env({"HTTPS_PROXY": "http://explicit-per-server:3128"})

    assert result["HTTPS_PROXY"] == "http://explicit-per-server:3128"
