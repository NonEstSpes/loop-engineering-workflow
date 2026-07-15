"""FastAPI web app for the daemon.

Phase 1 endpoints:
- ``GET /api/health`` — daemon health (status, scheduler, uptime).
- ``GET /api/state`` — daemon config and HITL strategy.

Phase 2+ will add /api/approvals, /api/tasks/*, /api/eod, /api/events (SSE).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import frontmatter
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from devflow.batch.eod_handler import EodHandler
from devflow.config import Config, HitlStrategy
from devflow.daemon.approval_store import ApprovalStore
from devflow.daemon.events import GLOBAL_TOPIC, EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.todo_api import rewrite_todo_line, serialize_todo
from devflow.todo import parse_todo

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    scheduler: str
    uptime_seconds: int
    current_task: str | None = None
    pending_approvals: int = 0
    batch_store_pending: int = 0
    errors_last_24h: int = 0


class StateResponse(BaseModel):
    hitl_strategy: str
    daemon: dict[str, Any]
    task_source: str


class ApprovalDecision(BaseModel):
    """Body of a POST /api/approvals/{thread_id} decision."""

    approved: bool
    reason: str = ""
    requested_changes: list[str] = Field(default_factory=list)


class EodPublishRequest(BaseModel):
    """Body of POST /api/eod/publish."""

    task_ids: list[str] = Field(default_factory=list)


class TaskCurrentResponse(BaseModel):
    """Active task + current graph node (node is None until runner tracks it)."""

    task_id: str | None = None
    node: str | None = None


class TaskQueueResponse(BaseModel):
    """Pending task queue (introspection limited without a live task source)."""

    queue: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class RunTaskRequest(BaseModel):
    """Body of POST /api/tasks/run."""

    task_id: str | None = None
    repo_path: str | None = None


class RunTaskResponse(BaseModel):
    """Response of POST /api/tasks/run."""

    run_id: str
    task_id: str | None = None
    status: str


class TodoPatchRequest(BaseModel):
    """Body of PATCH /api/todo/{line_no}."""

    priority: int | None = None
    status: str | None = None


class ConfigPatchRequest(BaseModel):
    """Body of PATCH /api/config. All fields optional."""

    hitl_strategy: str | None = None
    daemon: dict[str, Any] | None = None


class HitlSwitchRequest(BaseModel):
    """Body of PUT /api/config/hitl."""

    strategy: str


class AgentPromptUpdate(BaseModel):
    """Body of PUT /api/agents/{name}/prompt."""

    system_prompt: str


def create_app(
    app_cfg: Config,
    locks: DaemonLocks,
    event_bus: EventBus,
    runner: Any | None = None,
    approval_store: ApprovalStore | None = None,
    eod_handler: EodHandler | None = None,
    scheduler: Any | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    ``runner`` is the WorkflowRunner (or None in Phase 1 tests). It will be
    used by Phase 2+ endpoints to query task progress and approvals.

    ``approval_store`` is the registry of pending human approvals (Task 4).
    When provided, this exposes ``GET /api/approvals`` and
    ``POST /api/approvals/{thread_id}``, and populates ``pending_approvals``
    in ``GET /api/health``.

    ``eod_handler`` is the EOD batch review/publish orchestrator (Task 5).
    When provided, this exposes the ``/api/eod*`` endpoints and populates
    ``batch_store_pending`` in ``GET /api/health``.
    """
    app = FastAPI(title="devflow-daemon", version="0.1.0")
    start_time = time.monotonic()
    _state: dict[str, Any] = {"current_task": None}

    # Expose runner/scheduler/cfg on app.state so control endpoints can reach them.
    app.state.runner = runner
    app.state.scheduler = scheduler
    app.state.cfg = app_cfg
    # Cross-loop-safe (threading, not asyncio) mutex for on-demand runs.
    app.state._run_lock = threading.Lock()

    # CORS: allow the Vite dev server origin(s) when configured.
    cors_origins = app_cfg.workflow.daemon.cors_origins
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _is_task_running() -> bool:
        """Check if the task_run lock is currently held.

        ``asyncio.Lock.locked()`` is a synchronous method and reflects the
        lock's state regardless of which event loop holds it, so this works
        both when called from within the running request loop and from a
        different thread/loop (e.g. a scheduler thread acquiring the lock).
        """
        return locks.task_run().locked()

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        uptime = int(time.monotonic() - start_time)
        task_running = _is_task_running()
        pending = len(approval_store.get_pending()) if approval_store else 0
        batch_pending = 0
        if eod_handler is not None:
            try:
                batch_pending = eod_handler._store.count_pending()  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - defensive
                logger.debug("Failed to count pending batch entries")
        return HealthResponse(
            status="degraded" if task_running else "healthy",
            scheduler="busy" if task_running else "running",
            uptime_seconds=uptime,
            current_task=_state.get("current_task"),
            pending_approvals=pending,
            batch_store_pending=batch_pending,
        )

    @app.get("/api/state", response_model=StateResponse)
    async def state() -> StateResponse:
        daemon_cfg = app_cfg.workflow.daemon
        return StateResponse(
            hitl_strategy=app_cfg.workflow.hitl_strategy,
            daemon={
                "enabled": daemon_cfg.enabled,
                "task_schedule": daemon_cfg.task_schedule,
                "eod_schedule": daemon_cfg.eod_schedule,
                "port": daemon_cfg.port,
                "approval_timeout_hours": daemon_cfg.approval_timeout_hours,
                "approval_on_timeout": daemon_cfg.approval_on_timeout,
            },
            task_source=app_cfg.workflow.task_source,
        )

    def _entry_summary(entry: Any) -> dict[str, Any]:
        return {
            "id": entry.id,
            "task_id": entry.task_id,
            "task_title": entry.task_title,
            "branch_name": entry.branch_name,
            "final_verdict": entry.final_verdict.value if entry.final_verdict else None,
            "status": entry.status,
            "created_at": entry.created_at,
        }

    @app.get("/api/tasks/current", response_model=TaskCurrentResponse)
    async def tasks_current() -> TaskCurrentResponse:
        return TaskCurrentResponse(
            task_id=_state.get("current_task"),
            node=None,
        )

    @app.get("/api/tasks/queue", response_model=TaskQueueResponse)
    async def tasks_queue() -> TaskQueueResponse:
        return TaskQueueResponse(
            queue=[],
            note="queue introspection not available without an active task source",
        )

    @app.get("/api/tasks/done")
    async def tasks_done() -> list[dict[str, Any]]:
        if eod_handler is None:
            return []
        try:
            entries = eod_handler._store.list_all(status="published")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - defensive
            return []
        return [_entry_summary(e) for e in entries]

    @app.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str) -> dict[str, Any]:
        if eod_handler is None:
            raise HTTPException(status_code=404, detail="No batch store available")
        entries = eod_handler._store.get_by_task(task_id)  # type: ignore[attr-defined]
        if not entries:
            raise HTTPException(status_code=404, detail=f"No entry for task {task_id}")
        # Most recent entry (get_by_task returns oldest-first).
        return entries[-1].model_dump(mode="json")

    @app.post("/api/tasks/run", response_model=RunTaskResponse, status_code=202)
    async def run_task(req: RunTaskRequest) -> RunTaskResponse:
        """Trigger a workflow run on demand.

        If ``task_id`` is provided, runs that specific task; otherwise runs
        the next task(s) by priority (``runner.run_all``). The run executes
        in a background thread so the response returns immediately.
        Returns ``409`` if a task is already running.
        """
        run_lock = app.state._run_lock  # type: ignore[attr-defined]
        runner = app.state.runner  # type: ignore[attr-defined]
        if runner is None:
            raise HTTPException(status_code=503, detail="No runner configured")
        if not run_lock.acquire(blocking=False):
            current = _state.get("current_task")
            raise HTTPException(
                status_code=409,
                detail=f"Task already running: {current or 'unknown'}",
            )

        repo = req.repo_path or "."
        run_id = str(uuid.uuid4())

        async def _run_in_background() -> None:
            try:
                await asyncio.to_thread(_execute_run, runner, req.task_id, repo)
            finally:
                run_lock.release()

        asyncio.create_task(_run_in_background())
        return RunTaskResponse(
            run_id=run_id, task_id=req.task_id, status="started"
        )

    # -------------------------------------------------------------------
    # Control: TODO management
    # -------------------------------------------------------------------

    @app.get("/api/todo")
    async def list_todo() -> list[dict[str, Any]]:
        """List all TODO.md entries (re-read from disk each call)."""
        todo_path = Path(app_cfg.workflow.todo_path)
        items = parse_todo(todo_path)
        return serialize_todo(items)

    @app.patch("/api/todo/{line_no}")
    async def patch_todo(line_no: int, req: TodoPatchRequest) -> dict[str, Any]:
        """Update a single TODO line's priority and/or status (atomic disk write)."""
        todo_path = Path(app_cfg.workflow.todo_path)
        try:
            return rewrite_todo_line(
                todo_path, line_no,
                priority=req.priority, status=req.status,
            )
        except ValueError as exc:
            msg = str(exc)
            if "not found" in msg or "not a task" in msg:
                raise HTTPException(status_code=404, detail=msg) from exc
            raise HTTPException(status_code=422, detail=msg) from exc

    # -------------------------------------------------------------------
    # Control: config management
    # -------------------------------------------------------------------

    _RESTART_ONLY_DAEMON_FIELDS = {"port", "serve_frontend", "frontend_dist"}

    def _config_view() -> dict[str, Any]:
        """Serialize the current in-memory config into a JSON view."""
        d = app_cfg.workflow.daemon
        return {
            "task_source": app_cfg.workflow.task_source,
            "hitl_strategy": app_cfg.workflow.hitl_strategy,
            "todo_path": app_cfg.workflow.todo_path,
            "human_in_the_loop": app_cfg.workflow.human_in_the_loop,
            "daemon": {
                "enabled": d.enabled,
                "task_schedule": d.task_schedule,
                "eod_schedule": d.eod_schedule,
                "port": d.port,
                "approval_timeout_hours": d.approval_timeout_hours,
                "approval_on_timeout": d.approval_on_timeout,
                "serve_frontend": d.serve_frontend,
                "frontend_dist": d.frontend_dist,
            },
            "forge": {
                "provider": app_cfg.workflow.forge.provider,
                "target_branch": app_cfg.workflow.forge.target_branch,
                "actions": app_cfg.workflow.forge.actions,
            },
        }

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        return _config_view()

    @app.patch("/api/config")
    async def patch_config(req: ConfigPatchRequest) -> dict[str, Any]:
        """Mutate in-memory config. Schedule changes trigger scheduler.reschedule."""
        if req.hitl_strategy is not None:
            if req.hitl_strategy not in HitlStrategy.ALL:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid hitl_strategy: {req.hitl_strategy}",
                )
            app_cfg.workflow.hitl_strategy = req.hitl_strategy

        if req.daemon:
            for key in req.daemon:
                if key in _RESTART_ONLY_DAEMON_FIELDS:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Field '{key}' requires daemon restart",
                    )
            sched_obj = app.state.scheduler
            if "task_schedule" in req.daemon or "eod_schedule" in req.daemon:
                if sched_obj is not None:
                    try:
                        sched_obj.reschedule(
                            task_schedule=req.daemon.get("task_schedule"),
                            eod_schedule=req.daemon.get("eod_schedule"),
                        )
                    except ValueError as exc:
                        raise HTTPException(
                            status_code=422, detail=str(exc)
                        ) from exc
                else:
                    if "task_schedule" in req.daemon:
                        app_cfg.workflow.daemon.task_schedule = req.daemon["task_schedule"]
                    if "eod_schedule" in req.daemon:
                        app_cfg.workflow.daemon.eod_schedule = req.daemon["eod_schedule"]
            if "approval_timeout_hours" in req.daemon:
                app_cfg.workflow.daemon.approval_timeout_hours = req.daemon["approval_timeout_hours"]
            if "approval_on_timeout" in req.daemon:
                app_cfg.workflow.daemon.approval_on_timeout = req.daemon["approval_on_timeout"]

        return _config_view()

    @app.get("/api/config/diff")
    async def config_diff() -> dict[str, Any]:
        """Compare in-memory config to the workflow.yaml on disk."""
        from devflow.config import load_workflow_config

        config_dir = getattr(app.state, "config_dir", "config")
        disk_path = Path(config_dir) / "workflow.yaml"
        if not disk_path.exists():
            return {"changed": [], "clean": False, "note": "workflow.yaml not found"}
        try:
            disk_cfg = load_workflow_config(disk_path)
        except Exception as exc:
            return {"changed": [], "clean": False, "note": str(exc)}

        current = _config_view()
        disk_view = {
            "task_source": disk_cfg.task_source,
            "hitl_strategy": disk_cfg.hitl_strategy,
            "todo_path": disk_cfg.todo_path,
            "human_in_the_loop": disk_cfg.human_in_the_loop,
            "daemon": {
                "task_schedule": disk_cfg.daemon.task_schedule,
                "eod_schedule": disk_cfg.daemon.eod_schedule,
                "approval_timeout_hours": disk_cfg.daemon.approval_timeout_hours,
                "approval_on_timeout": disk_cfg.daemon.approval_on_timeout,
            },
        }
        changed: list[dict[str, Any]] = []
        for field, disk_val in disk_view.items():
            if field == "daemon":
                for dk, dv in disk_val.items():
                    cv = current["daemon"][dk]
                    if cv != dv:
                        changed.append({"field": f"daemon.{dk}", "in_memory": cv, "on_disk": dv})
            elif current.get(field) != disk_val:
                changed.append({"field": field, "in_memory": current.get(field), "on_disk": disk_val})
        return {"changed": changed, "clean": len(changed) == 0}

    @app.post("/api/config/save")
    async def save_config() -> dict[str, Any]:
        """Persist the current in-memory config to workflow.yaml (atomic)."""
        config_dir = getattr(app.state, "config_dir", "config")
        wf_path = Path(config_dir) / "workflow.yaml"
        data = {
            "task_source": app_cfg.workflow.task_source,
            "max_rework_iterations": app_cfg.workflow.max_rework_iterations,
            "human_in_the_loop": app_cfg.workflow.human_in_the_loop,
            "default_branch": app_cfg.workflow.default_branch,
            "pr_target_branch": app_cfg.workflow.pr_target_branch,
            "corporate_report_channels": app_cfg.workflow.corporate_report_channels,
            "todo_path": app_cfg.workflow.todo_path,
            "hitl_strategy": app_cfg.workflow.hitl_strategy,
            "daemon": {
                "enabled": app_cfg.workflow.daemon.enabled,
                "task_schedule": app_cfg.workflow.daemon.task_schedule,
                "eod_schedule": app_cfg.workflow.daemon.eod_schedule,
                "port": app_cfg.workflow.daemon.port,
                "approval_timeout_hours": app_cfg.workflow.daemon.approval_timeout_hours,
                "approval_on_timeout": app_cfg.workflow.daemon.approval_on_timeout,
                "serve_frontend": app_cfg.workflow.daemon.serve_frontend,
                "frontend_dist": app_cfg.workflow.daemon.frontend_dist,
            },
            "forge": {
                "provider": app_cfg.workflow.forge.provider,
                "target_branch": app_cfg.workflow.forge.target_branch,
                "actions": app_cfg.workflow.forge.actions,
            },
        }
        content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
        dir_ = wf_path.parent
        fd, tmp_name = tempfile.mkstemp(dir=str(dir_), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp_name, wf_path)
        except Exception as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise HTTPException(status_code=500, detail=f"Failed to persist: {exc}") from exc
        return {"path": str(wf_path)}

    # -------------------------------------------------------------------
    # Control: HITL strategy switch
    # -------------------------------------------------------------------

    @app.put("/api/config/hitl")
    async def switch_hitl(req: HitlSwitchRequest) -> dict[str, Any]:
        """Switch the HITL strategy at runtime + re-evaluate the EOD cron job."""
        if req.strategy not in HitlStrategy.ALL:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid strategy: {req.strategy}. Must be one of {sorted(HitlStrategy.ALL)}",
            )
        old = app_cfg.workflow.hitl_strategy
        app_cfg.workflow.hitl_strategy = req.strategy

        sched_obj = app.state.scheduler
        if sched_obj is not None:
            should_enable_eod = req.strategy == HitlStrategy.END_OF_DAY
            was_end_of_day = old == HitlStrategy.END_OF_DAY
            if should_enable_eod != was_end_of_day:
                sched_obj.set_eod_job(enabled=should_enable_eod, repo_path=".")
        logger.info("HITL strategy switched: %s -> %s", old, req.strategy)
        return {"strategy": req.strategy, "previous": old}

    # -------------------------------------------------------------------
    # Control: agent prompts
    # -------------------------------------------------------------------

    @app.get("/api/agents")
    async def list_agents() -> list[dict[str, Any]]:
        return [
            {
                "name": a.name,
                "provider": a.provider,
                "model": a.model,
                "temperature": a.temperature,
                "has_prompt": bool(a.system_prompt),
            }
            for a in app_cfg.agents.values()
        ]

    @app.get("/api/agents/{name}")
    async def get_agent(name: str) -> dict[str, Any]:
        a = app_cfg.agents.get(name)
        if a is None:
            raise HTTPException(status_code=404, detail=f"Unknown agent: {name}")
        return {
            "name": a.name,
            "provider": a.provider,
            "model": a.model,
            "temperature": a.temperature,
            "system_prompt": a.system_prompt,
            "skills": a.skills,
            "tools": a.tools,
            "auto_approve": a.auto_approve,
        }

    @app.put("/api/agents/{name}/prompt")
    async def update_agent_prompt(name: str, req: AgentPromptUpdate) -> dict[str, Any]:
        a = app_cfg.agents.get(name)
        if a is None:
            raise HTTPException(status_code=404, detail=f"Unknown agent: {name}")
        a.system_prompt = req.system_prompt  # in-memory, instant effect on next run
        logger.info("Agent prompt updated in-memory: %s", name)
        return {"name": name, "status": "updated"}

    @app.post("/api/agents/{name}/save")
    async def save_agent(name: str) -> dict[str, Any]:
        a = app_cfg.agents.get(name)
        if a is None:
            raise HTTPException(status_code=404, detail=f"Unknown agent: {name}")
        config_dir = getattr(app.state, "config_dir", "config")
        agents_dir = Path(config_dir) / "agents"
        agent_path = agents_dir / f"{name}.md"
        post = frontmatter.Post(a.system_prompt.lstrip("\n"))
        post.metadata = {
            "name": a.name,
            "provider": a.provider,
            "model": a.model,
            "temperature": a.temperature,
        }
        if a.auto_approve:
            post.metadata["auto_approve"] = True
        if a.skills:
            post.metadata["skills"] = a.skills
        if a.tools:
            post.metadata["tools"] = a.tools
        content = frontmatter.dumps(post)
        dir_ = agent_path.parent
        fd, tmp_name = tempfile.mkstemp(dir=str(dir_), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp_name, agent_path)
        except Exception as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise HTTPException(status_code=500, detail=f"Failed to persist: {exc}") from exc
        return {"path": str(agent_path)}

    @app.get("/api/events")
    async def event_stream() -> EventSourceResponse:
        """Server-Sent Events stream of all daemon events.

        Subscribes to the EventBus global topic and forwards each event as an
        SSE frame (``event: <name>`` + ``data: <json>``). The client connects
        via ``new EventSource('/api/events')``. A 15s ``ping`` heartbeat keeps
        proxies/browsers from closing idle connections.
        """
        queue = await event_bus.subscribe(GLOBAL_TOPIC)

        async def event_generator() -> AsyncGenerator[dict[str, str], None]:
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        # Heartbeat keepalive (prevents proxy/browser timeouts).
                        yield {"event": "ping", "data": "{}"}
                        continue
                    yield {
                        "event": msg.get("event", "message"),
                        "data": json.dumps(msg),
                    }
            finally:
                # Remove this subscriber's queue on disconnect so the EventBus
                # does not accumulate dead queues (one per reconnect).
                await event_bus.unsubscribe(GLOBAL_TOPIC, queue)

        return EventSourceResponse(event_generator())

    if approval_store is not None:

        @app.get("/api/approvals")
        async def list_approvals() -> list[dict[str, Any]]:
            return approval_store.get_pending()

        @app.post("/api/approvals/{thread_id}")
        async def resolve_approval(thread_id: str, decision: ApprovalDecision) -> dict[str, Any]:
            resolved = approval_store.resolve(
                thread_id,
                {
                    "approved": decision.approved,
                    "reason": decision.reason,
                    "requested_changes": decision.requested_changes,
                },
            )
            if not resolved:
                raise HTTPException(
                    status_code=404, detail=f"Unknown thread_id: {thread_id}"
                )
            return {"status": "resolved", "thread_id": thread_id}

    if eod_handler is not None:

        @app.get("/api/eod")
        async def list_eod_pending() -> list[dict[str, Any]]:
            return [_entry_summary(e) for e in eod_handler.list_pending()]

        @app.post("/api/eod/finalize")
        async def finalize_eod() -> dict[str, Any]:
            pending = eod_handler.finalize()
            return {"pending_count": len(pending)}

        @app.post("/api/eod/publish")
        async def publish_eod(req: EodPublishRequest) -> dict[str, Any]:
            return eod_handler.publish_selected(req.task_ids)

        @app.get("/api/eod/entries/{entry_id}")
        async def get_eod_entry(entry_id: int) -> dict[str, Any]:
            entry = eod_handler._store.get_entry(entry_id)  # type: ignore[attr-defined]
            if entry is None:
                raise HTTPException(status_code=404, detail=f"Unknown entry id: {entry_id}")
            return entry.model_dump(mode="json")

    def set_current_task(task_id: str | None) -> None:
        """Allow the runner to report which task is currently active."""
        _state["current_task"] = task_id

    # Expose the setter so the runner can update state.
    app.state.set_current_task = set_current_task  # type: ignore[attr-defined]

    # Serve the built frontend SPA in production (when the dist dir exists).
    # Registered AFTER all /api/* routes so the SPA fallback never shadows API
    # endpoints; the handler additionally 404s any path starting with "api".
    daemon_cfg = app_cfg.workflow.daemon
    if daemon_cfg.serve_frontend:
        # Normalize + absolutize dist_path once so the containment check in
        # spa_fallback is reliable (commonpath needs canonical, absolute paths).
        dist_path = os.path.abspath(os.path.normpath(daemon_cfg.frontend_dist))
        index_file = os.path.join(dist_path, "index.html")
        if os.path.isdir(dist_path) and os.path.isfile(index_file):
            from fastapi.staticfiles import StaticFiles

            # Mount static assets (JS/CSS/images) under /assets.
            assets_dir = os.path.join(dist_path, "assets")
            if os.path.isdir(assets_dir):
                app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

            # SPA fallback: any non-/api GET serves index.html (or a real file).
            @app.get("/{full_path:path}")
            async def spa_fallback(full_path: str) -> FileResponse:
                # Never intercept API routes.
                if full_path.startswith("api"):
                    raise HTTPException(status_code=404)
                # Serve a specific static file if it exists (and is under dist),
                # else index.html.
                if full_path:
                    candidate = os.path.abspath(
                        os.path.normpath(os.path.join(dist_path, full_path))
                    )
                    # Containment check: prevent path traversal (e.g.
                    # %2e%2e-encoded ".." escapes that os.path.join would
                    # follow). Reject the request explicitly rather than leaking
                    # a file or serving the SPA shell.
                    if os.path.commonpath([candidate, dist_path]) != dist_path:
                        raise HTTPException(status_code=404)
                    if os.path.isfile(candidate):
                        return FileResponse(candidate)
                return FileResponse(index_file)

            logger.info("Serving frontend SPA from %s", dist_path)
        else:
            logger.warning(
                "Frontend dist not found at %s; daemon serves API only. "
                "Run `npm run build` in frontend/ to build the SPA.",
                dist_path,
            )

    return app


def _execute_run(runner: Any, task_id: str | None, repo_path: str) -> None:
    """Synchronous run wrapper called in a background thread.

    Calls ``runner.run_task`` (specific task) or ``runner.run_all`` (next
    by priority). Exceptions are logged; the lock is released by the caller.
    """
    try:
        if task_id:
            runner.run_task(task_id=task_id, repo_path=repo_path)
        else:
            runner.run_all(repo_path=repo_path)
    except Exception:
        logger.exception("On-demand run failed (task_id=%s)", task_id)


def run_web_server(
    app_cfg: Config,
    locks: DaemonLocks,
    event_bus: EventBus,
    runner: Any | None = None,
    approval_store: ApprovalStore | None = None,
    eod_handler: EodHandler | None = None,
    app: FastAPI | None = None,
) -> None:
    """Run the uvicorn server (blocking). Called from the daemon entry point.

    ``approval_store`` (Task 4) is forwarded to ``create_app`` so the
    ``/api/approvals`` endpoints can be exposed when the daemon wires it in.

    ``eod_handler`` (Task 5) is forwarded so the ``/api/eod*`` endpoints
    can be exposed when the daemon wires it in.

    ``app`` (Task 6) is an optional pre-built FastAPI application. When
    provided it is served directly; otherwise the app is built here via
    ``create_app``. ``run_daemon`` builds the app explicitly so it can wire
    ``runner._on_task_change = app.state.set_current_task`` before serving.
    """
    import uvicorn

    if app is None:
        app = create_app(
            app_cfg, locks, event_bus, runner,
            approval_store=approval_store, eod_handler=eod_handler,
        )
    port = app_cfg.workflow.daemon.port
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
