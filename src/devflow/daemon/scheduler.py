"""APScheduler configuration for the daemon.

Registers cron jobs:
- ``task_run``: runs ``WorkflowRunner.run_all`` on the configured schedule.
- ``eod_review``: runs EOD batch-review on the configured schedule (only
  when ``hitl_strategy == end_of_day`` — Phase 4 implements the handler).

Jobs use ``max_instances=1, coalesce=True`` so overlapping runs are
skipped/merged rather than queued.
"""

from __future__ import annotations

import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from devflow.config import Config, HitlStrategy
from devflow.daemon.runner import WorkflowRunner

logger = logging.getLogger(__name__)


class DaemonScheduler:
    """Wraps APScheduler BackgroundScheduler for daemon cron jobs."""

    def __init__(self, app_cfg: Config, runner: WorkflowRunner) -> None:
        self._cfg = app_cfg
        self._runner = runner
        self._scheduler = BackgroundScheduler(daemon=True)
        self._lock = threading.Lock()
        self._jobs_registered = False

    @property
    def is_running(self) -> bool:
        """True if the scheduler is currently running."""
        return self._scheduler.running

    @property
    def job_count(self) -> int:
        """Number of registered jobs."""
        return len(self._scheduler.get_jobs())

    def start(self) -> None:
        """Start the scheduler (does not register jobs)."""
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("Scheduler started")

    def shutdown(self) -> None:
        """Shut down the scheduler, waiting for active jobs to finish."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    def register_jobs(self, repo_path: str) -> None:
        """Register cron jobs for task runs and (optionally) EOD review.

        Idempotent: calling twice does not duplicate jobs.
        """
        with self._lock:
            if self._jobs_registered:
                return

            daemon_cfg = self._cfg.workflow.daemon

            # Task run job — runs run_all on the configured cron schedule.
            trigger = CronTrigger.from_crontab(daemon_cfg.task_schedule)
            self._scheduler.add_job(
                self._run_all_wrapper,
                trigger=trigger,
                id="task_run",
                max_instances=1,
                coalesce=True,
                kwargs={"repo_path": repo_path},
                replace_existing=True,
            )
            logger.info("Registered task_run job with schedule: %s", daemon_cfg.task_schedule)

            # EOD review job — only in end_of_day strategy.
            if self._cfg.workflow.hitl_strategy == HitlStrategy.END_OF_DAY:
                eod_trigger = CronTrigger.from_crontab(daemon_cfg.eod_schedule)
                self._scheduler.add_job(
                    self._run_eod_wrapper,
                    trigger=eod_trigger,
                    id="eod_review",
                    max_instances=1,
                    coalesce=True,
                    kwargs={"repo_path": repo_path},
                    replace_existing=True,
                )
                logger.info("Registered eod_review job with schedule: %s", daemon_cfg.eod_schedule)

            self._jobs_registered = True

    def _run_all_wrapper(self, repo_path: str) -> None:
        """Job handler: run all open tasks. Catches exceptions so APScheduler
        doesn't kill the scheduler on a single failure."""
        try:
            logger.info("task_run job triggered")
            self._runner.run_all(repo_path=repo_path)
        except Exception:
            logger.exception("task_run job failed")

    def _run_eod_wrapper(self, repo_path: str) -> None:
        """Job handler: run EOD batch-review. Phase 4 implements the handler."""
        try:
            logger.info("eod_review job triggered (not yet implemented in Phase 1)")
            # Phase 4 will call: self._eod_handler.run_review(repo_path)
        except Exception:
            logger.exception("eod_review job failed")
