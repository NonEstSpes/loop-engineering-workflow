# Architecture

## Graph overview

```mermaid
flowchart TD
classDef startEnd fill:#e1f5e1,stroke:#2e7d32,stroke-width:2px;
classDef human fill:#fff3e0,stroke:#ef6c00,stroke-width:2px;
classDef compute fill:#e3f2fd,stroke:#1565c0,stroke-width:2px;
classDef checker fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px;
classDef reporting fill:#ffebee,stroke:#c62828,stroke-width:2px;
    subgraph Init
        orchestrator[orchestrator]:::compute
        task_fetcher[task_fetcher]:::compute
    end
    subgraph Planning
        plan_approval[plan_approval]:::human
        planner[planner]:::compute
    end
    subgraph Implementation
        maker[maker]:::compute
        self_review[self_review]:::compute
    end
    subgraph Review
        aggregate_checker[aggregate_checker]:::checker
        run_checker[run_checker]:::checker
    end
    subgraph Reporting
        reporter[reporter]:::reporting
    end
    end[END]:::startEnd
    start[START]:::startEnd
    start --> orchestrator
    aggregate_checker -->|needs rework| maker
    aggregate_checker -->|approved / escalate / max rework| reporter
    maker --> self_review
    orchestrator --> task_fetcher
    plan_approval -->|approved| maker
    plan_approval -->|rejected / error| reporter
    planner --> plan_approval
    run_checker --> aggregate_checker
    self_review -->|error| reporter
    self_review -->|ok| run_checker
    task_fetcher --> planner
    reporter --> end
```

## Nodes

| Node | Responsibility |
|------|----------------|
| `orchestrator` | Initializes state, resets reducers, logs the task. |
| `task_fetcher` | Loads a task from the configured `TaskSource` by ID or fetches the first open task. |
| `planner` | Generates an implementation `Plan` with steps, files to touch, and tests. |
| `plan_approval` | Human-in-the-loop gate. Auto-approves when `human_in_the_loop=false` or `auto_approve=true`. |
| `maker` | Checks out a fresh git worktree, applies file operations, runs tests, and commits. |
| `self_review` | Reviews the diff against the plan and reports issues. |
| `run_checker` | Runs a single checker subagent. Dispatched in parallel for `checker_a`, `checker_b`, `checker_c`. |
| `aggregate_checker` | Aggregates checker reports into `final_verdict` and increments `rework_count`. |
| `reporter` | Produces PR description, corporate report, and updates the external task tracker. |

## Human-in-the-loop

Plan approval uses LangGraph's `interrupt()`. When the graph hits the
`plan_approval` node it pauses and stores the interrupt payload. Resume by
calling `graph.invoke(..., config)` with a `resume` value such as:

```python
{
    "approved": True,
    "reason": "Plan looks good",
    "requested_changes": [],
}
```

## Persistence

`build_graph()` uses `langgraph.checkpoint.memory.InMemorySaver` by default.
Pass a custom `checkpointer` (for example, a SQLite or Postgres saver) to
enable resumption across process restarts.
