# Task sources

Task sources adapt external trackers to the `TaskSource` interface. They are
registered in `devflow/mcp/factory.py`.

## Built-in sources

| Name | Description |
|------|-------------|
| `mock` | Canned tasks for local development and tests. |
| `jira` | Jira REST API / MCP server stub. |
| `redmine` | Redmine REST API / MCP server stub. |

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
