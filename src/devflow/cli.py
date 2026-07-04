"""Command-line interface for the DevFlow workflow."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from devflow.config import Config, load_config
from devflow.graph import build_graph, run_workflow
from devflow.mcp.factory import build_task_source
from devflow.state import Task, WorkflowState
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


def _load_app_cfg(config_dir: str) -> Config:
    cfg = load_config(config_dir)
    return cfg


@app.command()
def run(
    task_id: str | None = typer.Option(None, "--task-id", "-t", help="Specific task ID to process"),
    repo_path: str | None = typer.Option(
        None, "--repo-path", "-r", help="Path to the target git repository"
    ),
    config_dir: str = typer.Option(
        "config", "--config-dir", "-c", help="Directory containing agent/provider configs"
    ),
    thread_id: str | None = typer.Option(
        None, "--thread-id", help="LangGraph thread ID for persistence"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
) -> None:
    """Run the workflow for a single task."""
    _setup_logging(verbose)
    app_cfg = _load_app_cfg(config_dir)
    configure_tracing()

    task_source = build_task_source(app_cfg.workflow)
    final_state = run_workflow(
        app_cfg=app_cfg,
        repo_path=repo_path,
        task_id=task_id,
        task_source=task_source,
        thread_id=thread_id,
    )
    _print_final_state(final_state)


@app.command()
def run_all(
    repo_path: str | None = typer.Option(
        None, "--repo-path", "-r", help="Path to the target git repository"
    ),
    config_dir: str = typer.Option(
        "config", "--config-dir", "-c", help="Directory containing agent/provider configs"
    ),
    limit: int = typer.Option(10, "--limit", "-l", help="Maximum number of open tasks to process"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
) -> None:
    """Run the workflow for all open tasks."""
    _setup_logging(verbose)
    app_cfg = _load_app_cfg(config_dir)
    configure_tracing()

    task_source = build_task_source(app_cfg.workflow)
    tasks = task_source.fetch_tasks(status="open", limit=limit)
    if not tasks:
        console.print("No open tasks found.")
        raise typer.Exit(0)

    console.print(f"Processing {len(tasks)} open task(s)...")
    for task in tasks:
        console.print(f"\n[bold]Task {task.id}:[/bold] {task.title}")
        final_state = run_workflow(
            app_cfg=app_cfg,
            repo_path=repo_path,
            task_source=task_source,
            task_id=task.id,
        )
        _print_final_state(final_state)

    task_source.close()


@app.command()
def validate_config(
    config_dir: str = typer.Option(
        "config", "--config-dir", "-c", help="Directory containing agent/provider configs"
    ),
) -> None:
    """Validate the agent and provider configuration."""
    app_cfg = _load_app_cfg(config_dir)

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
def visualize(
    config_dir: str = typer.Option(
        "config", "--config-dir", "-c", help="Directory containing agent/provider configs"
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write the Mermaid diagram to a file"
    ),
) -> None:
    """Generate a Mermaid diagram of the workflow graph."""
    app_cfg = _load_app_cfg(config_dir)
    graph = build_graph(app_cfg=app_cfg)
    mermaid = draw_enhanced_mermaid(graph)
    if output:
        output.write_text(mermaid, encoding="utf-8")
        console.print(f"Diagram written to {output}")
    else:
        console.print(mermaid)


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
