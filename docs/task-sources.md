# Task sources

Task sources adapt external trackers to the `TaskSource` interface. They are
registered in `devflow/mcp/factory.py`.

## Built-in sources

| Name | Description |
|------|-------------|
| `mock` | Canned tasks for local development and tests. |
| `jira` | Jira REST API / MCP server stub. |
| `redmine` | Redmine via the [`runekaagaard/mcp-redmine`](https://github.com/runekaagaard/mcp-redmine) MCP server. |

## Redmine source

The `redmine` source wraps the external `runekaagaard/mcp-redmine` MCP server,
launched over stdio via `uvx` (requires [`uv`](https://docs.astral.sh/uv/) in
`PATH`). All Redmine REST access is delegated to the server's
`redmine_request` tool; the adapter only maps issues onto the `Task` model.

- `fetch_tasks()` returns open issues assigned to the current user
  (`assigned_to_id=me`, `status_id=open`).
- `get_task_details(id)` fetches a single issue by id.
- `update_task_status(id, status, comment)` transitions an issue and adds a
  note (used by the reporter to close tasks by final verdict).

Status names from the reporter (`resolved`, `rejected`, `pending`,
`escalated`) are resolved to Redmine status ids via `/issue_statuses.json`
(cached) and fall back to passing the name through.

Environment variables:

| Variable | Description |
|----------|-------------|
| `REDMINE_URL` | Tracker base URL (required). |
| `REDMINE_API_KEY` | REST API key (required). |
| `REDMINE_HOST_HEADER` | Optional `Host` header (reverse proxy / vhost). |
| `REDMINE_MCP_COMMAND` | MCP server launch command (default `uvx`). |
| `REDMINE_MCP_ARGS` | MCP server args (default `--from mcp-redmine mcp-redmine`). |

## Adding a new MCP adapter

1. Create `devflow/mcp/<name>.py`.
2. Subclass `TaskSource` and implement:
   - `fetch_tasks(status, limit) -> list[Task]`
   - `get_task_details(task_id) -> Task`
   - `close() -> None`
   - Optionally `update_task_status(task_id, status, comment)`
3. Register the class in `devflow/mcp/factory.py`:

```python
from devflow.mcp.my_source import MyTaskSource

_TASK_SOURCE_REGISTRY: dict[str, type[TaskSource]] = {
    "mock": MockTaskSource,
    "my_source": MyTaskSource,
    ...
}
```

4. Add configuration handling in `build_task_source()` if the source needs
   environment variables or extra settings.
5. Update `config/workflow.yaml`:

```yaml
task_source: my_source
```

## Task model

```python
class Task(BaseModel):
    id: str
    title: str
    description: str
    status: str = "open"
    metadata: dict[str, Any] = Field(default_factory=dict)
```
