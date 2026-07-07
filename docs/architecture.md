# Architecture

## Graph overview

```mermaid
flowchart TD
    classDef startEnd fill:#e1f5e1,stroke:#2e7d32,stroke-width:2px;
    classDef human fill:#fff3e0,stroke:#ef6c00,stroke-width:2px;
    classDef compute fill:#e3f2fd,stroke:#1565c0,stroke-width:2px;
    classDef checker fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px;
    classDef reporting fill:#ffebee,stroke:#c62828,stroke-width:2px;
    classDef research fill:#e8f5e9,stroke:#1b5e20,stroke-width:2px;
    
    subgraph Init
        orchestrator["Orchestrator"]:::compute
        task_fetcher["Task Fetcher"]:::compute
    end
    
    subgraph Planning
        plan_approval["Plan Approval"]:::human
        planner["Planner"]:::compute
    end
    
    subgraph Implementation
        maker["Maker"]:::compute
        self_review["Self Review"]:::compute
    end
    
    subgraph Review
        aggregate_checker["Aggregate Checker"]:::checker
        run_checker["Run Checker"]:::checker
    end
    
    subgraph Research
        research["Research"]:::research
    end
    
    subgraph Reporting
        reporter["Reporter"]:::reporting
    end
    
    end_node["END"]:::startEnd
    start_node["START"]:::startEnd
    
    start_node --> orchestrator
    orchestrator --> task_fetcher
    task_fetcher --> planner
    
    planner --> plan_approval
    plan_approval -->|"Approved"| maker
    plan_approval -->|"Rejected"| reporter
    
    maker --> self_review
    self_review -->|"OK"| run_checker
    self_review -->|"Error"| reporter
    
    run_checker --> aggregate_checker
    aggregate_checker -->|"Rework"| maker
    aggregate_checker -->|"Done"| reporter
    
    planner -.->|"Research"| research
    maker -.->|"Research"| research
    self_review -.->|"Research"| research
    run_checker -.->|"Research"| research
    
    research -->|"Result"| planner
    research -->|"Result"| maker
    research -->|"Result"| self_review
    research -->|"Result"| run_checker
    
    reporter --> end_node
```

## Nodes

| Node | Responsibility |
|------|----------------|
| `orchestrator` | Initializes state, resets reducers, logs the task. |
| `task_fetcher` | Loads a task from the configured `TaskSource` by ID or fetches the first open task. |
| `planner` | Generates an implementation `Plan` with steps, files to touch, and tests. Can request on-demand research via `Command(goto="research")`. |
| `plan_approval` | Human-in-the-loop gate. Auto-approves when `human_in_the_loop=false` or `auto_approve=true`. |
| `maker` | Checks out a fresh git worktree, applies file operations, runs tests, and commits. Can request on-demand research before applying changes. |
| `self_review` | Reviews the diff against the plan and reports issues. Can request on-demand research. |
| `run_checker` | Runs a single checker subagent. Dispatched in parallel for `checker_a`, `checker_b`, `checker_c`. |
| `aggregate_checker` | Aggregates checker reports into `final_verdict` and increments `rework_count`. |
| `research` | Runs an on-demand research query against configured sources (MCP, git, filesystem, web) and returns the result to the caller node. |
| `reporter` | Produces PR description, corporate report, and updates the external task tracker. |

## On-demand research

Any agent node can return `Command(goto="research", update={"research_request": ...})` to gather additional context. The `research` node:

1. Reads `research_request` from state.
2. Optionally interrupts for human clarification when `request_human_clarification=true`.
3. Runs enabled sources from `config/research_sources.yaml`.
4. Aggregates findings into a `ResearchResult` stored in `last_research_result`.
5. Routes back to the caller node identified by `research_request.caller`.

The caller node sees the result on its next execution and can request further research up to `max_research_calls_per_node`.

## Research sources

Sources are configured in `config/research_sources.yaml`:

```yaml
request_human_clarification: false
max_research_calls_per_node: 3
sources:
  - name: graphify_mcp
    driver: graphify_mcp
    enabled: false
    config:
      server_url: ${GRAPHIFY_MCP_URL:-http://localhost:8000}
  - name: git_tools
    driver: git_tools
    enabled: true
    config:
      repo_path: .
```

Built-in drivers:

| Driver | Description |
|--------|-------------|
| `graphify_mcp` | Symbol/file search through a Graphify MCP server. |
| `mcp_generic` | Calls a configurable tool on any MCP server. |
| `git_tools` | `git grep` and `git log --grep` over the repository. |
| `file_system` | File name and content search under a root directory. |
| `web_search` | Web search via `duckduckgo-search` (optional dependency). |

New drivers can be added under `src/devflow/research/sources/` and registered in `SourceFactory.default()` without changing the `research` node itself.

## Human-in-the-loop

Plan approval and research both use LangGraph's `interrupt()`. When the graph hits the `plan_approval` or `research` node it pauses and stores the interrupt payload. Resume by calling `graph.invoke(..., config)` with a `Command(resume=...)` value.

Example resume for plan approval:

```python
{
    "approved": True,
    "reason": "Plan looks good",
    "requested_changes": [],
}
```

Example resume for research clarification:

```python
"Refined research query"
```

## Persistence

`build_graph()` uses `langgraph.checkpoint.memory.InMemorySaver` by default.
Pass a custom `checkpointer` (for example, a SQLite or Postgres saver) to
enable resumption across process restarts.
