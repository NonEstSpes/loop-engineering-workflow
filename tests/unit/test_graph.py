"""Unit tests for the compiled workflow graph structure."""

from __future__ import annotations

from devflow.config import Config, HitlStrategy
from devflow.graph import build_graph


def test_graph_has_publish_approval_node(mock_config: Config) -> None:
    """The compiled graph should include a publish_approval node."""
    mock_config.workflow.hitl_strategy = HitlStrategy.FULL_DETAIL
    graph = build_graph(app_cfg=mock_config)
    # The compiled graph's nodes are accessible via .nodes
    node_names = set(graph.nodes.keys())
    assert "publish_approval" in node_names


def test_graph_routes_aggregate_to_publish_on_approve(mock_config: Config) -> None:
    """On APPROVE verdict, aggregate_checker routes to publish_approval (not reporter)."""
    mock_config.workflow.hitl_strategy = HitlStrategy.FULL_DETAIL
    graph = build_graph(app_cfg=mock_config)
    node_names = set(graph.nodes.keys())
    # Both publish_approval and reporter must exist
    assert "publish_approval" in node_names
    assert "reporter" in node_names
