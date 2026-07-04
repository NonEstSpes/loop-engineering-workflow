"""LangSmith tracing configuration."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def configure_tracing() -> None:
    """Ensure LangSmith environment variables are set for tracing."""
    project = os.getenv("LANGSMITH_PROJECT")
    if not project:
        os.environ["LANGSMITH_PROJECT"] = "devflow-super"
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    logger.info("LangSmith tracing enabled: project=%s", os.environ["LANGSMITH_PROJECT"])
