"""Unit tests for the enhanced Mermaid visualization."""

from __future__ import annotations

from devflow.graph import build_graph
from devflow.visualization import draw_enhanced_mermaid


def test_draw_enhanced_mermaid_includes_all_nodes(mock_config: object) -> None:
    """The generated diagram contains every workflow node."""
    graph = build_graph(app_cfg=mock_config)
    mermaid = draw_enhanced_mermaid(graph)

    expected_nodes = [
        "orchestrator",
        "task_fetcher",
        "planner",
        "plan_approval",
        "maker",
        "self_review",
        "run_checker",
        "aggregate_checker",
        "reporter",
        "START",
        "END",
    ]
    for node in expected_nodes:
        assert node in mermaid, f"Node {node} missing from diagram"


def test_draw_enhanced_mermaid_has_styling_and_subgraphs(mock_config: object) -> None:
    """The diagram uses Mermaid classes and subgraphs."""
    graph = build_graph(app_cfg=mock_config)
    mermaid = draw_enhanced_mermaid(graph)

    assert "classDef startEnd" in mermaid
    assert "classDef human" in mermaid
    assert "classDef compute" in mermaid
    assert "classDef checker" in mermaid
    assert "classDef reporting" in mermaid
    assert "subgraph Init" in mermaid
    assert "subgraph Planning" in mermaid
    assert "subgraph Implementation" in mermaid
    assert "subgraph Review" in mermaid
    assert "subgraph Reporting" in mermaid


def test_draw_enhanced_mermaid_labels_conditional_edges(mock_config: object) -> None:
    """Conditional edges carry human-readable labels."""
    graph = build_graph(app_cfg=mock_config)
    mermaid = draw_enhanced_mermaid(graph)

    assert "|approved|" in mermaid
    assert "|rejected / error|" in mermaid
    assert "|ok|" in mermaid
    assert "|error|" in mermaid
    assert "|needs rework|" in mermaid
    assert "|approved / escalate / max rework|" in mermaid
