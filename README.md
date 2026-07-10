# langgraph-devflow-super

LangGraph-based development workflow with human-in-the-loop approval,
parallel checker subagents, and isolated git worktrees.

## Overview

`devflow-super` orchestrates a multi-agent software development workflow:

1. **Orchestrator** initializes workflow state.
2. **Task Fetcher** loads a task from an external tracker (mock, Jira, Redmine).
3. **Planner** drafts an implementation plan.
4. **Plan Approval** pauses for human review unless `auto_approve` is enabled.
5. **Maker** implements the plan in a fresh git worktree.
6. **Self Review** reviews the generated diff.
7. **Checkers** run correctness, security, and maintainability audits in parallel.
8. **Aggregate Checker** combines checker reports and decides whether to rework.
9. **Research** (on-demand) gathers context from MCP servers, git, files, or web when agents need it.
10. **Reporter** generates PR descriptions, corporate reports, and updates the tracker.

## Install

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in API keys for the providers you use.

## Quick Start

Validate configuration:

```bash
devflow-super validate-config
```

Run the workflow for the first open task:

```bash
devflow-super run --repo-path ./my-repo --verbose
```

Run for a specific task:

```bash
devflow-super run --task-id MOCK-1 --repo-path ./my-repo
```

Process all open tasks:

```bash
devflow-super run-all --repo-path ./my-repo --limit 5
```

List tasks with status, progress, and problems:

```bash
devflow-super list-tasks --format table
devflow-super list-tasks --start-task-id MOCK-1
```

Visualize the graph:

```bash
devflow-super visualize --output graph.mmd
```

## Architecture Summary

The workflow is modeled as a LangGraph `StateGraph` over `WorkflowState`.
Persistence uses `InMemorySaver` by default; supply a custom checkpointer
for production use. Conditional edges route between maker, reporter, checker,
and research nodes based on plan approval, self-review status, aggregate
checker verdicts, and on-demand research requests.

See [`docs/architecture.md`](docs/architecture.md) for the full Mermaid
diagram and node descriptions.

For a complete operational guide — installation, API keys, configuration,
monitoring, and troubleshooting — see [`docs/runbooks/operations.md`](docs/runbooks/operations.md).
