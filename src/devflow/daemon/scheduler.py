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

from devflow.batch.eod_handler import EodHandler
from devflow.config import Config, HitlStrategy
from devflow.daemon.runner import WorkflowRunner

logger = logging.getLogger(__name__)


class DaemonScheduler:
    """Wraps APScheduler BackgroundScheduler for daemon cron jobs."""

    def __init__(
        self,
        app_cfg: Config,
        runner: WorkflowRunner,
        eod_handler: EodHandler | None = None,
    ) -> None:
        self._cfg = app_cfg
        self._runner = runner
        self._eod_handler = eod_handler
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
        """Shut down the scheduler WITHOUT waiting for active jobs to finish.

        Uses ``wait=False`` so the daemon exits promptly on stop/restart.
        In-flight workflow runs are abandoned (consistent with the spec's
        "accept loss" policy for InMemory state); the startup sweep cleans
        any orphaned worktrees on the next start.
        """
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

    def reschedule(
        self,
        task_schedule: str | None = None,
        eod_schedule: str | None = None,
    ) -> None:
        """Reschedule cron jobs to new schedules.

        Validates each cron string via :class:`CronTrigger.from_crontab`
        (raises ``ValueError`` on invalid syntax). Only re-registers jobs
        that currently exist; safe to call before ``register_jobs``.
        """
        with self._lock:
            if task_schedule is not None:
                trigger = CronTrigger.from_crontab(task_schedule)  # raises ValueError
                if self._scheduler.get_job("task_run") is not None:
                    self._scheduler.reschedule_job(
                        "task_run", trigger=trigger
                    )
                self._cfg.workflow.daemon.task_schedule = task_schedule
                logger.info("Rescheduled task_run to: %s", task_schedule)

            if eod_schedule is not None:
                trigger = CronTrigger.from_crontab(eod_schedule)  # raises ValueError
                if self._scheduler.get_job("eod_review") is not None:
                    self._scheduler.reschedule_job(
                        "eod_review", trigger=trigger
                    )
                self._cfg.workflow.daemon.eod_schedule = eod_schedule
                logger.info("Rescheduled eod_review to: %s", eod_schedule)

    def set_eod_job(self, enabled: bool, repo_path: str = ".") -> None:
        """Enable or disable the EOD review cron job at runtime.

        Called when HITL strategy is switched to/from ``end_of_day`` so the
        EOD job matches the current strategy without a daemon restart.
        """
        with self._lock:
            if enabled:
                if self._scheduler.get_job("eod_review") is None:
                    eod_trigger = CronTrigger.from_crontab(
                        self._cfg.workflow.daemon.eod_schedule
                    )
                    self._scheduler.add_job(
                        self._run_eod_wrapper,
                        trigger=eod_trigger,
                        id="eod_review",
                        max_instances=1,
                        coalesce=True,
                        kwargs={"repo_path": repo_path},
                        replace_existing=True,
                    )
                    logger.info("Enabled eod_review job")
            else:
                if self._scheduler.get_job("eod_review") is not None:
                    self._scheduler.remove_job("eod_review")
                    logger.info("Disabled eod_review job")

    def _run_all_wrapper(self, repo_path: str) -> None:
        """Job handler: run all open tasks. Catches exceptions so APScheduler
        doesn't kill the scheduler on a single failure."""
        # Concurrency: relies on APScheduler max_instances=1; no cross-loop lock (see HANDOFF.md).
        try:
            logger.info("task_run job triggered")
            self._runner.run_all(repo_path=repo_path)
        except Exception:
            logger.exception("task_run job failed")

    def _run_eod_wrapper(self, repo_path: str) -> None:
        """Job handler: run EOD batch-review + publish-all.

        Concurrency note: no asyncio lock is acquired here. The daemon is
        multi-threaded (APScheduler thread + uvicorn thread pool), so an
        asyncio.Lock cannot be safely shared across loops. Mutual exclusion
        relies on APScheduler max_instances=1 per job id + the task_schedule
        and eod_schedule being at different wall-clock times. If an operator
        sets them close together or a task run overruns past eod_schedule,
        overlap is possible — see HANDOFF.md known limitations.
        """
        try:
            if self._eod_handler is None:
                logger.info("eod_review job triggered but no handler configured")
                return
            logger.info("eod_review job triggered")
            self._eod_handler.finalize()
            self._eod_handler.publish_selected([])
        except Exception:
            logger.exception("eod_review job failed")
