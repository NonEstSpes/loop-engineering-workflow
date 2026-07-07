"""On-demand research node and helpers for agent-driven research loops."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import Command, interrupt

from devflow.config import Config
from devflow.research.factory import SourceFactory
from devflow.research.schemas import ResearchFinding, ResearchRequest, ResearchResult
from devflow.state import WorkflowError, WorkflowState

logger = logging.getLogger(__name__)


def research_node(
    state: WorkflowState,
    *,
    app_cfg: Config,
    source_factory: SourceFactory | None = None,
) -> dict[str, Any]:
    """Execute a research request against configured sources.

    The node is a no-op when no ``research_request`` is present. When a request
    exists, it runs the enabled sources, aggregates the findings into a
    ``ResearchResult``, and routes back to the caller node.
    """
    request = state.get("research_request")
    if request is None:
        return {}

    cfg = app_cfg.research_sources
    call_count = state.get("research_call_count", 0)

    if call_count >= cfg.max_research_calls_per_node:
        logger.warning("Research budget exhausted for caller %s", request.caller)
        result = ResearchResult(
            query=request.query,
            caller=request.caller,
            summary="Research budget exhausted for this node.",
            errors=["max_research_calls_per_node exceeded"],
        )
        return _finish_research(request, result)

    if cfg.request_human_clarification:
        clarification = interrupt(
            {
                "query": request.query,
                "context": request.context,
                "message": "Refine the research query or leave empty to keep it as-is.",
            }
        )
        if clarification and isinstance(clarification, str):
            request = request.model_copy(update={"query": clarification})

    factory = source_factory or SourceFactory.default()
    built_sources = factory.build(cfg)

    if request.source_names:
        active_sources = {
            name: source
            for name, source in built_sources.items()
            if name in request.source_names
        }
    else:
        active_sources = built_sources

    findings: list[ResearchFinding] = []
    errors: list[str] = []
    sources_used: list[str] = []

    try:
        for name, source in active_sources.items():
            try:
                source_findings = source.search(request)
                findings.extend(source_findings)
                if source_findings:
                    sources_used.append(name)
            except Exception as exc:
                logger.warning("Research source %s failed: %s", name, exc)
                errors.append(f"{name}: {exc}")
    finally:
        for source in active_sources.values():
            try:
                source.close()
            except Exception as exc:
                logger.debug("Error closing source %s: %s", source.name, exc)

    summary = _build_summary(request, findings, sources_used, errors)
    result = ResearchResult(
        query=request.query,
        caller=request.caller,
        summary=summary,
        findings=findings,
        errors=errors,
        sources_used=sources_used,
    )

    logger.info(
        "Research finished for %s: %d findings from %s",
        request.caller,
        len(findings),
        sources_used,
    )
    return _finish_research(request, result, call_count)


def _build_summary(
    request: ResearchRequest,
    findings: list[ResearchFinding],
    sources_used: list[str],
    errors: list[str],
) -> str:
    """Build a concise text summary from findings."""
    lines: list[str] = [f"Research for: {request.query}"]
    if sources_used:
        lines.append(f"Sources used: {', '.join(sources_used)}")
    if errors:
        lines.append(f"Errors: {'; '.join(errors)}")
    if not findings:
        lines.append("No findings.")
        return "\n".join(lines)

    lines.append("Findings:")
    for finding in findings[:10]:
        title = finding.title or finding.source
        lines.append(f"- [{finding.source}] {title}: {finding.content[:300]}")
    if len(findings) > 10:
        lines.append(f"... and {len(findings) - 10} more findings")
    return "\n".join(lines)


def _finish_research(
    request: ResearchRequest,
    result: ResearchResult,
    call_count: int = 0,
) -> dict[str, Any]:
    """Return the state update that completes a research cycle."""
    return {
        "research_request": None,
        "last_research_result": result,
        "research_results": [result],
        "research_call_count": call_count + 1,
        "logs": [
            f"research: {request.caller} asked '{request.query}' -> "
            f"{len(result.findings)} findings from {result.sources_used}"
        ],
    }


def request_research_command(request: ResearchRequest) -> Command:
    """Return a Command that hands control to the research node."""
    return Command(
        goto="research",
        update={
            "research_request": request,
            "last_research_result": None,
        },
    )


def take_research_result(
    state: WorkflowState,
    caller: str,
) -> ResearchResult | None:
    """Peek at the last research result if it belongs to ``caller``.

    The caller is responsible for clearing the result in its own state update.
    """
    result = state.get("last_research_result")
    if result is not None and result.caller == caller:
        return result
    return None


def research_budget_exceeded(state: WorkflowState, app_cfg: Config) -> bool:
    """Return True if the current node has exhausted its research budget."""
    return state.get("research_call_count", 0) >= app_cfg.research_sources.max_research_calls_per_node


def format_research_context(result: ResearchResult | None) -> str:
    """Format a research result for inclusion in an agent prompt."""
    if result is None:
        return "No prior research result."
    context = f"Prior research result for '{result.query}':\n{result.summary}"
    if result.errors:
        context += "\nErrors: " + "; ".join(result.errors)
    return context


def research_error_state(node: str, message: str) -> dict[str, Any]:
    """Return a state update that records a research-handling error."""
    return {
        "error": WorkflowError(node=node, message=message),
        "logs": [f"{node}: research error - {message}"],
    }
