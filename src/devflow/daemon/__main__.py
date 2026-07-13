"""Entry point for ``python -m devflow.daemon``.

Loads config, runs startup sweep for orphan worktrees, creates the
scheduler + web app + runner, registers jobs, and starts the uvicorn
web server (blocking).

Usage:
    python -m devflow.daemon [--config-dir config] [--repo-path ./my-repo]
"""

from __future__ import annotations

import logging
import signal
import sys

from devflow.config import load_config
from devflow.daemon.approval_bridge import ApprovalBridge
from devflow.daemon.approval_store import ApprovalStore
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.runner import WorkflowRunner
from devflow.daemon.scheduler import DaemonScheduler
from devflow.daemon.sweep import cleanup_orphan_worktrees
from devflow.daemon.web import run_web_server
from devflow.notifications.factory import build_notification_channels

logger = logging.getLogger(__name__)


def run_daemon(config_dir: str = "config", repo_path: str = ".") -> None:
    """Start the daemon: config -> sweep -> scheduler -> web server.

    This function blocks (uvicorn.run is blocking). The scheduler runs in
    a background thread, so cron jobs fire independently of the web server.
    """
    logger.info("Starting devflow-daemon...")

    # 1. Load configuration.
    app_cfg = load_config(config_dir)
    daemon_cfg = app_cfg.workflow.daemon

    if not daemon_cfg.enabled:
        logger.error("Daemon is not enabled in config (daemon.enabled=false). Exiting.")
        sys.exit(1)

    # 2. Startup sweep: clean orphaned worktrees from previous crashes.
    logger.info("Running startup worktree sweep...")
    cleaned = cleanup_orphan_worktrees(repo_path)
    if cleaned:
        logger.info("Cleaned %d orphan worktree(s): %s", len(cleaned), cleaned)

    # 3. Create shared components.
    event_bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(app_cfg, event_bus, locks)

    # 3b. Create approval store + bridge for HITL strategies.
    approval_store = ApprovalStore()
    push_channels = build_notification_channels(app_cfg.workflow)
    bridge = ApprovalBridge(
        store=approval_store,
        push_channels=push_channels,
        approval_timeout_hours=daemon_cfg.approval_timeout_hours,
        on_timeout=daemon_cfg.approval_on_timeout,
    )
    # Recreate the runner with the bridge attached so run_task uses
    # run_workflow_interactive (pausing on plan/publish approval interrupts).
    runner = WorkflowRunner(app_cfg, event_bus, locks, approval_bridge=bridge)

    # 4. Create and start scheduler, register jobs.
    scheduler = DaemonScheduler(app_cfg, runner)
    scheduler.start()
    scheduler.register_jobs(repo_path)

    # 5. Graceful shutdown handler.
    # Uvicorn installs its own SIGINT/SIGTERM handlers that shadow these,
    # so this is a best-effort bonus. The reliable cleanup path is the
    # try/finally around run_web_server below.
    def _shutdown(signum: int, frame: object) -> None:
        logger.info("Received signal %s; uvicorn will exit and the finally block will clean up.", signum)
        scheduler.shutdown()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # 6. Start web server (blocking). Wrapped in try/finally so the scheduler
    # is always shut down when uvicorn returns (uvicorn installs its own
    # SIGINT/SIGTERM handlers that shadow ours, so the signal handler above
    # is a best-effort bonus, not the primary shutdown path).
    logger.info("Starting web server on 127.0.0.1:%d", daemon_cfg.port)
    try:
        run_web_server(app_cfg, locks, event_bus, runner, approval_store=approval_store)
    finally:
        logger.info("Web server stopped; shutting down scheduler...")
        scheduler.shutdown()
        logger.info("Daemon stopped.")


def main() -> None:
    """CLI wrapper for ``python -m devflow.daemon``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Parse minimal args; full CLI via typer can be added later.
    config_dir = "config"
    repo_path = "."
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--config-dir" and i + 1 < len(args):
            config_dir = args[i + 1]
            i += 2
        elif args[i] == "--repo-path" and i + 1 < len(args):
            repo_path = args[i + 1]
            i += 2
        else:
            i += 1

    run_daemon(config_dir=config_dir, repo_path=repo_path)


if __name__ == "__main__":
    main()
