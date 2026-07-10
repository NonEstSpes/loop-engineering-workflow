---
name: Operations Runbook
description: How to install, configure, launch, and monitor the DevFlow workflow.
version: 1.0.0
last_updated: 2026-07-10
maintained_by: DevFlow maintainers
---

# Operations Runbook

This runbook covers everything needed to get `langgraph-devflow-super` running in a local or self-hosted environment: installation, required API keys, configuration files, launch commands, where tasks come from, what artifacts are produced, and how to watch progress.

## Purpose and scope

`devflow-super` is a LangGraph-based autonomous development workflow. For each task it:

1. Fetches a task from a configured tracker.
2. Plans the implementation.
3. Asks for human approval (or auto-approves).
4. Implements the change in an isolated git worktree.
5. Self-reviews and runs parallel checker subagents.
6. Reports the result and updates the tracker.

This document is the operational source of truth. Architectural details live in [`docs/architecture.md`](../architecture.md), task-source internals in [`docs/task-sources.md`](../task-sources.md), and agent prompt format in [`docs/agent-config.md`](../agent-config.md).

## Prerequisites

- Python `>=3.11`.
- Git installed and available on `PATH`.
- A target repository to work on (the `--repo-path` argument).
- Network access to whichever LLM provider and task tracker you configure.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

To enable the optional web-search research driver, also install:

```bash
pip install -e ".[dev,research]"
```

The package installs the `devflow-super` CLI entry point.

## Configuration

Configuration is split across environment variables (`.env`) and YAML/markdown files under `config/`.

### Environment variables

Copy `.env.example` to `.env` and fill in the values for the providers and trackers you use.

```bash
cp .env.example .env
```

| Variable | Required for | Description |
|----------|--------------|-------------|
| `OPENAI_API_KEY` | OpenAI provider | API key for `openai` / `openai_compatible` provider. |
| `ANTHROPIC_API_KEY` | Anthropic provider | API key for Claude models. |
| `GOOGLE_API_KEY` | Google provider | API key for Gemini models. |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI | API key. |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI | Endpoint URL. |
| `KIMI_API_KEY` | Kimi provider | API key for `kimi` / `kimi-code` models. |
| `LANGSMITH_API_KEY` | LangSmith tracing | Optional; tracing is enabled by default under project `devflow-super`. |
| `LANGSMITH_PROJECT` | LangSmith tracing | Defaults to `devflow-super`. |
| `TASK_SOURCE_PROVIDER` | Task source | Legacy hint; real source is read from `config/workflow.yaml`. |
| `REDMINE_URL` | Redmine source | Tracker base URL. |
| `REDMINE_API_KEY` | Redmine source | REST API key. |
| `JIRA_URL` | Jira source | Tracker base URL. |
| `JIRA_USERNAME` | Jira source | User login. |
| `JIRA_API_TOKEN` | Jira source | API token. |
| `GITHUB_TOKEN` | GitHub | Used by future forge integrations / research tools. |
| `GITLAB_TOKEN` | GitLab | Used by future forge integrations / research tools. |
| `SLACK_WEBHOOK_URL` | Notifications | Corporate report channel. |
| `TEAMS_WEBHOOK_URL` | Notifications | Corporate report channel. |
| `WEB_SEARCH_API_KEY` | Web search | Optional; default engine is DuckDuckGo. |

Additional operational overrides:

| Variable | Effect |
|----------|--------|
| `DEVFLOW_PROVIDER_OVERRIDE` | Forces every agent to use this provider name. |
| `DEVFLOW_MODEL_OVERRIDE` | Forces every agent to use this model name. |
| `DEVFLOW_TEMPERATURE_OVERRIDE` | Forces every agent temperature (float). |
| `DEVFLOW_HUMAN_IN_THE_LOOP` | `true`/`1`/`yes` to enable approvals; `false` to disable. |
| `DEVFLOW_DEFAULT_BRANCH` | Overrides `default_branch` in `config/workflow.yaml`. |

### Provider configuration: `config/providers.yaml`

Each provider entry maps a name (used by agents) to a driver and credentials. Example:

```yaml
providers:
  openai:
    type: openai_compatible
    api_key: ${OPENAI_API_KEY}
    base_url: http://127.0.0.1:1234

  anthropic:
    type: anthropic
    api_key: ${ANTHROPIC_API_KEY}

  kimi:
    type: openai_compatible
    api_key: ${KIMI_API_KEY}
    base_url: https://api.kimi.com/coding/v1
    timeout: 120
    max_retries: 3

  ollama:
    type: ollama
    base_url: http://localhost:11434
```

Supported `type` values: `openai_compatible`, `openai`, `anthropic`, `google`, `azure`, `ollama`, `mock`.

### Workflow configuration: `config/workflow.yaml`

```yaml
task_source: mock          # mock | jira | redmine
max_rework_iterations: 3
human_in_the_loop: true
default_branch: main
pr_target_branch: main
corporate_report_channels:
  - console                # also supported: github, gitlab, slack
```

| Key | Description |
|-----|-------------|
| `task_source` | Which task tracker adapter to use. |
| `max_rework_iterations` | How many times a rejected/conditional result can be sent back to the maker. |
| `human_in_the_loop` | Whether `plan_approval` pauses for a human decision. |
| `default_branch` | Base branch for new worktrees and diffs. |
| `pr_target_branch` | Target branch for generated PR descriptions. |
| `corporate_report_channels` | Where the reporter publishes results. |

### Research sources: `config/research_sources.yaml`

Enabled sources are consulted when an agent asks for on-demand research. Example:

```yaml
request_human_clarification: false
max_research_calls_per_node: 3

sources:
  - name: git_tools
    driver: git_tools
    enabled: true
    config:
      repo_path: .

  - name: file_system
    driver: file_system
    enabled: true
    config:
      root: .

  - name: web_search
    driver: web_search
    enabled: false
    config:
      max_results: 5
      timeout: 30
```

Built-in drivers: `graphify_mcp`, `mcp_generic`, `git_tools`, `file_system`, `web_search`.

### Agent configuration: `config/agents/*.md`

Each file is a Markdown document with YAML frontmatter followed by the system prompt. Example `config/agents/planner.md`:

```yaml
---
name: planner
provider: kimi
model: kimi-code/kimi-for-coding
temperature: 0.3
auto_approve: false
---
```

| Frontmatter field | Description |
|-------------------|-------------|
| `name` | Agent identifier. |
| `provider` | Provider key from `config/providers.yaml`. |
| `model` | Model name passed to the provider. |
| `temperature` | Sampling temperature. |
| `max_tokens` | Optional cap on response length. |
| `auto_approve` | If `true`, `plan_approval` skips the human gate for this agent. |

Out of the box all agents point to `kimi` / `kimi-code/kimi-for-coding`. To run against OpenAI, change the agent `provider` to `openai` and set `OPENAI_API_KEY`.

## Validate configuration

Before launching the workflow, run:

```bash
devflow-super validate-config
```

This prints tables of loaded agents, providers, and workflow settings. Fix any missing provider or unknown model errors here before running tasks.

## Launch commands

All commands accept `--config-dir` (`-c`) and `--verbose` (`-v`). By default `.env` is loaded from the working directory; use `--env-file` to point elsewhere.

### Run one task

```bash
# First open task from the configured source
devflow-super run --repo-path ./my-repo --verbose

# Specific task
devflow-super run --task-id MOCK-1 --repo-path ./my-repo --verbose
```

### Run all open tasks

```bash
devflow-super run-all --repo-path ./my-repo --limit 5 --verbose
```

### List tasks and progress

```bash
devflow-super list-tasks --format table
devflow-super list-tasks --format json --limit 50

# List and immediately start a specific task
devflow-super list-tasks --start-task-id MOCK-1
```

### Visualize the workflow graph

```bash
devflow-super visualize --output graph.mmd
```

## Where tasks come from

Tasks are loaded by the `task_source` adapter defined in `config/workflow.yaml`.

| Source | Description | Required env vars |
|--------|-------------|-------------------|
| `mock` | Two canned tasks (`MOCK-1`, `MOCK-2`) for local testing. | None. |
| `jira` | Fetches from Jira REST API. | `JIRA_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN`. |
| `redmine` | Fetches from Redmine REST API. | `REDMINE_URL`, `REDMINE_API_KEY`. |

The default `config/workflow.yaml` uses `mock`. Switch to a real tracker by changing `task_source` and providing the corresponding credentials.

## What gets produced and where

For each processed task the workflow produces the following artifacts and state updates.

### Git artifacts

- A new branch named `devflow/{task_id}/{short-uuid}` is created.
- A fresh git worktree is created next to the repository (`{repo-name}-worktree-{short-uuid}`).
- The maker applies file operations inside the worktree and commits them with message `devflow({task_id}): {summary}`.
- The diff between the worktree branch and `default_branch` is captured in workflow state.

### State fields to watch

The CLI and logs expose these key fields from `WorkflowState`:

| Field | Meaning |
|-------|---------|
| `task` | Fetched task (id, title, description, status). |
| `plan` | Generated implementation plan. |
| `plan_approved` | Whether the plan was approved or auto-approved. |
| `worktree_path` | Path to the isolated worktree. |
| `branch_name` | Branch that holds the implementation commit. |
| `diff` | Full diff of the implementation. |
| `self_review_notes` | Findings from the self-review node. |
| `checker_reports` | Verdicts from `checker_a`, `checker_b`, `checker_c`. |
| `final_verdict` | `approve`, `reject`, `conditional`, or `escalate`. |
| `rework_count` | How many rework loops have occurred. |
| `pr_url` | Placeholder PR URL when a forge channel is enabled. |
| `report_url` | Report destination (`console`, `slack://posted`, etc.). |
| `error` | Captured `WorkflowError` if a node failed. |
| `logs` | Per-node log lines collected during the run. |

### Tracker updates

The reporter updates the source tracker status based on the final verdict:

| Final verdict | Tracker status |
|---------------|----------------|
| `approve` | `resolved` |
| `conditional` | `pending` |
| `reject` | `rejected` |
| `escalate` | `escalated` |

Mock tasks are updated only in memory for the current process.

### Corporate reports

Currently only `console` reporting is wired end-to-end. Future channels (`github`, `gitlab`, `slack`) produce placeholder URLs. The reporter logs the generated PR title and description at `INFO` level.

## How to monitor progress

### CLI progress table

`devflow-super list-tasks` shows one row per task:

| Column | Description |
|--------|-------------|
| `ID` | Task identifier. |
| `Title` | Task title from the tracker. |
| `Status` | Tracker status (`open`, `resolved`, etc.). |
| `Progress` | Workflow phase (`not started`, `planning`, `awaiting approval`, `implementing`, `self-review`, `checking`, `done`, `error`). |
| `Problems` | Human-readable issue summary, or `-`. |

Progress is derived from the latest checkpointed workflow state when available; otherwise it falls back to the tracker status.

### Verbose logs

Run any command with `--verbose` (`-v`) to see per-node logs, research requests, routing decisions, and LLM call metadata. The log format is:

```
YYYY-MM-DD HH:MM:SS LEVEL module.name: message
```

### LangSmith tracing

Tracing is enabled by default. Set `LANGSMITH_API_KEY` and optionally `LANGSMITH_PROJECT` to trace every graph invocation, node, and LLM call in LangSmith.

### State inspection (programmatic)

For custom dashboards, build the graph with a checkpointer and call `get_state()`:

```python
from devflow.config import load_config
from devflow.graph import build_graph
from devflow.mcp.factory import build_task_source

cfg = load_config("config")
source = build_task_source(cfg.workflow)
graph = build_graph(app_cfg=cfg, task_source=source, task_id="MOCK-1")
snapshot = graph.get_state({"configurable": {"thread_id": "MOCK-1"}})
print(snapshot.values)
```

## Human-in-the-loop

When `human_in_the_loop: true` and the agent is not `auto_approve`, the graph pauses at `plan_approval`. The interrupt payload contains the task title, description, plan summary, and steps.

Resume by invoking the graph with a `Command(resume=...)` value:

```python
from langgraph.types import Command

graph.invoke(
    Command(resume={
        "approved": True,
        "reason": "Plan looks good",
        "requested_changes": [],
    }),
    {"configurable": {"thread_id": "MOCK-1"}},
)
```

If the plan is rejected, the workflow routes directly to the reporter and ends without implementing.

On-demand research can also interrupt for clarification when `request_human_clarification: true` in `config/research_sources.yaml`.

## Example: first run with the mock source

1. Install the project:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -e ".[dev]"
   ```

2. Keep `config/workflow.yaml` using `task_source: mock`.

3. Configure at least one LLM provider in `.env` and `config/providers.yaml`. For example, set `KIMI_API_KEY`.

4. Validate:

   ```bash
   devflow-super validate-config
   ```

5. Run the first mock task against a real repo:

   ```bash
   devflow-super run --task-id MOCK-1 --repo-path ./my-repo --verbose
   ```

6. If `human_in_the_loop` is enabled, approve the plan when prompted.

7. Watch the worktree and branch appear next to `./my-repo`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Configuration error` on startup | Missing `.env` value referenced by `${...}` in YAML. | Fill in the variable or add a default (`${VAR:-default}`). |
| `No open tasks found` | Source has no tasks in `open` status. | Check tracker filters or switch to `mock`. |
| `Maker failed` / git errors | Target path is not a git repo or base branch is missing. | Ensure `--repo-path` points to a repository with `default_branch` checked out or fetchable. |
| LLM errors / timeouts | Provider key invalid or model unavailable. | Verify the key, `base_url`, and model name in `config/providers.yaml` and agent files. |
| `Unknown task source` | `task_source` value not in registry. | Use `mock`, `jira`, or `redmine`, or register a custom adapter. |
| Plan never pauses for approval | `human_in_the_loop: false` or `auto_approve: true`. | Check `config/workflow.yaml` and the agent frontmatter. |
| State lost between restarts | Using default `InMemorySaver`. | Pass a persistent checkpointer (SQLite/Postgres) to `build_graph()`. |

For development commands (lint, format, type check, tests), see [`docs/development.md`](../development.md).
