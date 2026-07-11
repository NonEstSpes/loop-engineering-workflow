"""Command-line interface for the DevFlow workflow."""

from __future__ import annotations

import functools
import json
import logging
import os
from pathlib import Path
from typing import Any, cast

import typer
from dotenv import load_dotenv
from langchain_core.runnables import RunnableConfig
from rich.console import Console
from rich.table import Table

from devflow.config import Config, load_config
from devflow.graph import build_graph, run_workflow, run_workflow_interactive
from devflow.mcp.base import TaskSource
from devflow.mcp.factory import build_task_source
from devflow.schemas import Plan
from devflow.state import CheckerVerdict, FinalVerdict, Task, WorkflowError, WorkflowState
from devflow.utils.tracing import configure_tracing
from devflow.visualization import draw_enhanced_mermaid

app = typer.Typer(help="LangGraph-based development workflow CLI")
console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _handle_errors(func: Any) -> Any:
    """Print friendly errors and exit with a non-zero code."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except typer.Exit:
            raise
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from exc

    return wrapper


def _telegram_enabled(app_cfg: Config, no_telegram: bool) -> bool:
    """Return True when Telegram human-in-the-loop should be used.

    Requires ``human_in_the_loop`` enabled, the user not opting out via
    ``--no-telegram``, both ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` set
    in the environment, and the optional ``httpx`` package installed.
    """
    if no_telegram or not app_cfg.workflow.human_in_the_loop:
        return False
    if not (os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")):
        return False
    try:
        import httpx  # noqa: F401
    except ImportError:
        logging.getLogger(__name__).warning(
            "Telegram credentials are set, but the optional 'httpx' package is "
            "not installed; falling back to non-interactive mode. Install it "
            "with `pip install -e '.[telegram]'`."
        )
        return False
    return True


def _build_telegram_approval_callback(channel: Any) -> Any:
    """Build an approval callback that drives plan approval via Telegram.

    The returned callable matches the signature expected by
    :func:`devflow.graph.run_workflow_interactive`: ``(payload, state) -> dict``.
    """
    from devflow.telegram_bridge import TelegramBridge

    bridge = TelegramBridge(channel)

    def _callback(payload: dict[str, Any], state: WorkflowState) -> dict[str, Any]:
        task = state.get("task")
        plan = state.get("plan")
        if task is None or plan is None:
            # Fall back to auto-approve if the expected state is missing.
            return {"approved": True, "reason": "No task/plan in state", "requested_changes": []}
        console.print(
            "[yellow]Plan approval required — waiting for your decision in Telegram...[/yellow]"
        )
        if not isinstance(plan, Plan):
            return {"approved": True, "reason": "Plan type unexpected", "requested_changes": []}
        task_obj = task if isinstance(task, Task) else Task(id=str(task), title="", description="")
        return bridge.request_plan_approval(task_obj, plan)

    return _callback


def _build_telegram_channel() -> Any:
    """Construct a TelegramChannel from env vars (httpx must be installed)."""
    from devflow.notifications.telegram import TelegramChannel

    return TelegramChannel({
        "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
    })


@app.callback()
def main(
    ctx: typer.Context,
    config_dir: str = typer.Option(
        "config", "--config-dir", "-c", help="Directory containing agent/provider configs"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
    env_file: Path | None = typer.Option(
        None, "--env-file", help="Path to a .env file to load"
    ),
) -> None:
    """Load environment, configure logging/tracing, and read application config."""
    if env_file is not None:
        load_dotenv(env_file)
    elif not ctx.resilient_parsing:
        load_dotenv()

    _setup_logging(verbose)

    try:
        app_cfg = load_config(config_dir)
    except Exception as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(2) from exc

    configure_tracing()
    ctx.obj = {"config": app_cfg, "verbose": verbose}


@app.command()
@_handle_errors
def run(
    ctx: typer.Context,
    task_id: str | None = typer.Option(
        None, "--task-id", "-t", help="Specific task ID to process"
    ),
    repo_path: str | None = typer.Option(
        None, "--repo-path", "-r", help="Path to the target git repository"
    ),
    thread_id: str | None = typer.Option(
        None, "--thread-id", help="LangGraph thread ID for persistence"
    ),
    no_telegram: bool = typer.Option(
        False, "--no-telegram", help="Disable Telegram human-in-the-loop approvals"
    ),
) -> None:
    """Run the workflow for a single task."""
    app_cfg: Config = ctx.obj["config"]
    task_source = build_task_source(app_cfg.workflow)
    try:
        if _telegram_enabled(app_cfg, no_telegram):
            channel = _build_telegram_channel()
            try:
                approval_callback = _build_telegram_approval_callback(channel)
                final_state = run_workflow_interactive(
                    app_cfg=app_cfg,
                    repo_path=repo_path,
                    task_id=task_id,
                    task_source=task_source,
                    thread_id=thread_id,
                    approval_callback=approval_callback,
                )
            finally:
                channel.close()
        else:
            final_state = run_workflow(
                app_cfg=app_cfg,
                repo_path=repo_path,
                task_id=task_id,
                task_source=task_source,
                thread_id=thread_id,
            )
        _print_final_state(final_state)
    finally:
        task_source.close()


@app.command()
@_handle_errors
def run_all(
    ctx: typer.Context,
    repo_path: str | None = typer.Option(
        None, "--repo-path", "-r", help="Path to the target git repository"
    ),
    limit: int = typer.Option(
        10, "--limit", "-l", help="Maximum number of open tasks to process"
    ),
    no_telegram: bool = typer.Option(
        False, "--no-telegram", help="Disable Telegram human-in-the-loop approvals"
    ),
) -> None:
    """Run the workflow for all open tasks."""
    app_cfg: Config = ctx.obj["config"]
    task_source = build_task_source(app_cfg.workflow)
    use_telegram = _telegram_enabled(app_cfg, no_telegram)
    channel = None
    if use_telegram:
        channel = _build_telegram_channel()
    try:
        tasks = task_source.fetch_tasks(status="open", limit=limit)
        if not tasks:
            console.print("No open tasks found.")
            raise typer.Exit(0)

        console.print(f"Processing {len(tasks)} open task(s)...")
        for task in tasks:
            console.print(f"\n[bold]Task {task.id}:[/bold] {task.title}")
            if use_telegram and channel is not None:
                approval_callback = _build_telegram_approval_callback(channel)
                final_state = run_workflow_interactive(
                    app_cfg=app_cfg,
                    repo_path=repo_path,
                    task_source=task_source,
                    task_id=task.id,
                    thread_id=task.id,
                    approval_callback=approval_callback,
                )
            else:
                final_state = run_workflow(
                    app_cfg=app_cfg,
                    repo_path=repo_path,
                    task_source=task_source,
                    task_id=task.id,
                    thread_id=task.id,
                )
            _print_final_state(final_state)
    finally:
        if channel is not None:
            channel.close()
        task_source.close()


@app.command()
@_handle_errors
def validate_config(ctx: typer.Context) -> None:
    """Validate the agent and provider configuration."""
    app_cfg: Config = ctx.obj["config"]

    table = Table(title="Agents")
    table.add_column("Name", style="cyan")
    table.add_column("Provider", style="magenta")
    table.add_column("Model", style="green")
    table.add_column("Auto-approve", style="yellow")
    for name, agent in app_cfg.agents.items():
        table.add_row(
            name,
            agent.provider,
            agent.model,
            "yes" if agent.auto_approve else "no",
        )
    console.print(table)

    providers = Table(title="Providers")
    providers.add_column("Name", style="cyan")
    providers.add_column("Base URL", style="green")
    for name, provider in app_cfg.providers.items():
        providers.add_row(name, provider.base_url or "<default>")
    console.print(providers)

    workflow = Table(title="Workflow")
    workflow.add_column("Key", style="cyan")
    workflow.add_column("Value", style="green")
    for key, value in app_cfg.workflow.model_dump().items():
        workflow.add_row(key, str(value))
    console.print(workflow)


@app.command()
@_handle_errors
def visualize(
    ctx: typer.Context,
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write the Mermaid diagram to a file"
    ),
) -> None:
    """Generate a Mermaid diagram of the workflow graph."""
    app_cfg: Config = ctx.obj["config"]
    graph = build_graph(app_cfg=app_cfg)
    mermaid = draw_enhanced_mermaid(graph)
    if output:
        output.write_text(mermaid, encoding="utf-8")
        console.print(f"Diagram written to {output}")
    else:
        console.print(mermaid)


@app.command(name="list-tasks")
@_handle_errors
def list_tasks(
    ctx: typer.Context,
    status: str = typer.Option(
        "open", "--status", "-s", help="Filter tasks by tracker status"
    ),
    limit: int = typer.Option(
        50, "--limit", "-l", help="Maximum number of tasks to show"
    ),
    start_task_id: str | None = typer.Option(
        None, "--start-task-id", help="Immediately run this task after listing"
    ),
    output_format: str = typer.Option(
        "table", "--format", "-f", help="Output format: table or json"
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress rich output"
    ),
) -> None:
    """List tasks with their status, workflow progress, and any problems."""
    app_cfg: Config = ctx.obj["config"]
    task_source = build_task_source(app_cfg.workflow)
    try:
        tasks = task_source.fetch_tasks(status=status, limit=limit)
        if not tasks:
            if not quiet:
                console.print("No tasks found.")
            raise typer.Exit(0)

        rows = [_task_row(task, app_cfg, task_source) for task in tasks]

        if output_format.lower() == "json":
            console.print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            _print_task_table(rows, quiet)

        if start_task_id is not None:
            console.print(f"\nStarting task {start_task_id}...")
            final_state = run_workflow(
                app_cfg=app_cfg,
                task_source=task_source,
                task_id=start_task_id,
                thread_id=start_task_id,
            )
            _print_final_state(final_state)
    finally:
        task_source.close()


def _task_row(task: Task, app_cfg: Config, task_source: TaskSource) -> dict[str, str]:
    """Build a display row for a single task."""
    state = _load_task_state(task.id, app_cfg, task_source)
    progress, problems = _derive_progress_and_problems(task, state)
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "progress": progress,
        "problems": problems,
    }


def _load_task_state(
    task_id: str, app_cfg: Config, task_source: TaskSource
) -> WorkflowState | None:
    """Load the latest workflow state for a task if a checkpointer has it."""
    try:
        graph = build_graph(app_cfg=app_cfg, task_source=task_source, task_id=task_id)
        config: RunnableConfig = {"configurable": {"thread_id": task_id}}
        snapshot = graph.get_state(config)
        values = snapshot.values if snapshot else None
        if values:
            return cast(WorkflowState, values)
    except Exception:
        # If the graph/checkpointer cannot return state, fall back to tracker data.
        pass
    return None


def _derive_progress_and_problems(
    task: Task, state: WorkflowState | None
) -> tuple[str, str]:
    """Return human-readable progress and problem summary for a task."""
    if state is not None:
        return _progress_from_state(state), _problems_from_state(state)
    return _progress_from_status(task.status), _problems_from_status(task.status)


def _progress_from_state(state: WorkflowState) -> str:
    error = state.get("error")
    if error is not None:
        return "error"

    if state.get("final_verdict") is not None:
        return "done"

    if state.get("checker_reports"):
        return "checking"

    if state.get("diff") is not None or state.get("self_review_notes") is not None:
        return "self-review"

    if state.get("plan_approved"):
        return "implementing"

    if state.get("plan") is not None:
        return "awaiting approval"

    if state.get("task") is not None:
        return "planning"

    return "not started"


def _problems_from_state(state: WorkflowState) -> str:
    error = state.get("error")
    if isinstance(error, WorkflowError):
        return error.message
    if error is not None:
        return str(error)

    verdict = state.get("final_verdict")
    if verdict in {FinalVerdict.REJECT, FinalVerdict.CONDITIONAL, FinalVerdict.ESCALATE}:
        reports = state.get("checker_reports", [])
        summaries = [
            f"{report.agent_name}: {report.summary}"
            for report in reports
            if report.verdict != CheckerVerdict.APPROVE
        ]
        prefix = f"verdict {verdict.value}"
        if summaries:
            return f"{prefix}; " + "; ".join(summaries)
        return prefix

    return "-"


def _progress_from_status(status: str) -> str:
    mapping = {
        "open": "not started",
        "in_progress": "implementing",
        "resolved": "done",
        "pending": "checking",
        "rejected": "error",
        "escalated": "escalated",
    }
    return mapping.get(status, status)


def _problems_from_status(status: str) -> str:
    mapping = {
        "rejected": "rejected",
        "escalated": "escalated",
        "pending": "conditional",
    }
    return mapping.get(status, "-")


def _print_task_table(rows: list[dict[str, str]], quiet: bool) -> None:
    if quiet:
        for row in rows:
            console.print(f"{row['id']}\t{row['status']}\t{row['progress']}\t{row['problems']}")
        return

    table = Table(title="Tasks")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="green")
    table.add_column("Status", style="yellow")
    table.add_column("Progress", style="magenta")
    table.add_column("Problems", style="red")
    for row in rows:
        table.add_row(
            row["id"],
            row["title"],
            row["status"],
            row["progress"],
            row["problems"],
        )
    console.print(table)


def _print_final_state(state: WorkflowState) -> None:
    task = state.get("task")
    plan = state.get("plan")
    verdict = state.get("final_verdict")
    error = state.get("error")

    table = Table(title="Workflow Result")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Task", task.id if isinstance(task, Task) else str(task))
    table.add_row("Plan", plan.summary if plan else "<none>")
    table.add_row("Verdict", verdict.value if verdict else "<none>")
    table.add_row("Error", error.message if error else "<none>")
    console.print(table)

    if state.get("pr_url"):
        console.print(f"PR URL: {state['pr_url']}")
    if state.get("report_url"):
        console.print(f"Report URL: {state['report_url']}")


if __name__ == "__main__":
    app()
