"""Enhanced Mermaid visualization for the DevFlow state graph."""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

# Styling classes for Mermaid nodes.
_CLASS_DEFINITIONS = """
classDef startEnd fill:#e1f5e1,stroke:#2e7d32,stroke-width:2px;
classDef human fill:#fff3e0,stroke:#ef6c00,stroke-width:2px;
classDef compute fill:#e3f2fd,stroke:#1565c0,stroke-width:2px;
classDef checker fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px;
classDef reporting fill:#ffebee,stroke:#c62828,stroke-width:2px;
""".strip()

# Which Mermaid class a node belongs to.
_NODE_CLASSES: dict[str, str] = {
    "__start__": "startEnd",
    "__end__": "startEnd",
    "orchestrator": "compute",
    "task_fetcher": "compute",
    "planner": "compute",
    "plan_approval": "human",
    "maker": "compute",
    "self_review": "compute",
    "run_checker": "checker",
    "aggregate_checker": "checker",
    "reporter": "reporting",
}

# Subgraph membership by node id.
_SUBGRAPHS: dict[str, str] = {
    "orchestrator": "Init",
    "task_fetcher": "Init",
    "planner": "Planning",
    "plan_approval": "Planning",
    "maker": "Implementation",
    "self_review": "Implementation",
    "run_checker": "Review",
    "aggregate_checker": "Review",
    "reporter": "Reporting",
}

# Labels for conditional edges. Non-conditional edges have no label.
_EDGE_LABELS: dict[tuple[str, str], str] = {
    ("plan_approval", "maker"): "approved",
    ("plan_approval", "reporter"): "rejected / error",
    ("self_review", "run_checker"): "ok",
    ("self_review", "reporter"): "error",
    ("aggregate_checker", "maker"): "needs rework",
    ("aggregate_checker", "reporter"): "approved / escalate / max rework",
}


def _node_id(raw: str) -> str:
    """Return a Mermaid-safe node identifier."""
    return raw.replace("__", "")


def _node_label(raw: str) -> str:
    """Return a human-readable node label."""
    if raw == "__start__":
        return "START"
    if raw == "__end__":
        return "END"
    return raw


def draw_enhanced_mermaid(graph: CompiledStateGraph) -> str:
    """Generate a styled Mermaid flowchart of the compiled state graph.

    The output groups nodes into logical subgraphs and highlights conditional
    edges so that the workflow structure is easier to understand at a glance.
    """
    inner = graph.get_graph()
    nodes = sorted(inner.nodes)
    edges = inner.edges

    lines = ["flowchart TD"]
    lines.append(_CLASS_DEFINITIONS)

    # Render subgraphs in a fixed order so the diagram is deterministic.
    subgraph_order = ["Init", "Planning", "Implementation", "Review", "Reporting"]
    subgraph_nodes: dict[str, list[str]] = {name: [] for name in subgraph_order}
    for node in nodes:
        subgraph = _SUBGRAPHS.get(node)
        if subgraph:
            subgraph_nodes[subgraph].append(node)

    for subgraph in subgraph_order:
        members = subgraph_nodes[subgraph]
        if not members:
            continue
        lines.append(f"    subgraph {subgraph}")
        for node in members:
            node_class = _NODE_CLASSES.get(node, "compute")
            lines.append(
                f"        {_node_id(node)}[{_node_label(node)}]:::{node_class}"
            )
        lines.append("    end")

    # START and END live outside subgraphs for clarity.
    for node in nodes:
        if node in {"__start__", "__end__"}:
            node_class = _NODE_CLASSES[node]
            lines.append(
                f"    {_node_id(node)}[{_node_label(node)}]:::{node_class}"
            )

    for edge in edges:
        source = edge.source
        target = edge.target
        label = _EDGE_LABELS.get((source, target), "")
        if edge.conditional and not label:
            label = "conditional"
        arrow = f"    {_node_id(source)} -->"
        if label:
            arrow += f"|{label}|"
        arrow += f" {_node_id(target)}"
        lines.append(arrow)

    return "\n".join(lines) + "\n"
