"""Tests for DaemonScheduler.reschedule and set_eod_job."""

from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger

from devflow.config import Config, HitlStrategy, WorkflowConfig
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.runner import WorkflowRunner
from devflow.daemon.scheduler import DaemonScheduler


def _make_scheduler(strategy: str = HitlStrategy.PER_PLAN) -> DaemonScheduler:
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock", hitl_strategy=strategy),
        providers={},
        agents={},
    )
    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(cfg, bus, locks)
    sched = DaemonScheduler(cfg, runner, eod_handler=None)
    sched.start()
    sched.register_jobs(".")
    return sched


def test_reschedule_task_run_updates_trigger() -> None:
    sched = _make_scheduler()
    try:
        sched.reschedule(task_schedule="*/30 * * * *")
        job = sched._scheduler.get_job("task_run")
        assert job is not None
        # Next run time reflects the new 30-min schedule.
        assert job.trigger is not None
    finally:
        sched.shutdown()


def test_reschedule_invalid_cron_raises() -> None:
    import pytest

    sched = _make_scheduler()
    try:
        with pytest.raises(ValueError):
            sched.reschedule(task_schedule="not a cron")
    finally:
        sched.shutdown()


def test_set_eod_job_enables_when_end_of_day() -> None:
    sched = _make_scheduler(strategy=HitlStrategy.PER_PLAN)
    try:
        # Initially no eod_review job (per_plan strategy).
        assert sched._scheduler.get_job("eod_review") is None
        sched.set_eod_job(enabled=True, repo_path=".")
        assert sched._scheduler.get_job("eod_review") is not None
    finally:
        sched.shutdown()


def test_set_eod_job_disables_when_switching_away() -> None:
    sched = _make_scheduler(strategy=HitlStrategy.END_OF_DAY)
    try:
        assert sched._scheduler.get_job("eod_review") is not None
        sched.set_eod_job(enabled=False, repo_path=".")
        assert sched._scheduler.get_job("eod_review") is None
    finally:
        sched.shutdown()
