"""Pydantic schemas for structured LLM outputs used by graph nodes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ApprovalResponse(BaseModel):
    """Response from the plan approval agent."""

    approved: bool
    reason: str
    requested_changes: list[str] = Field(default_factory=list)


class ResearchFinding(BaseModel):
    """A single finding returned by a research source."""

    source: str
    query: str = ""
    title: str = ""
    content: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchResult(BaseModel):
    """Aggregated result from a research subagent."""

    query: str
    caller: str = ""
    summary: str = ""
    findings: list[ResearchFinding] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    sources_used: list[str] = Field(default_factory=list)


class ResearchRequest(BaseModel):
    """Request for the on-demand research subagent."""

    query: str
    caller: str = ""
    source_names: list[str] = Field(default_factory=list)
    depth: int = Field(default=1, ge=1, le=3)
    max_results: int = Field(default=10, ge=1)
    context: str = ""


class PlanStep(BaseModel):
    """A single step in an implementation plan."""

    id: str
    description: str
    files_to_touch: list[str] = Field(default_factory=list)
    tests_to_add: list[str] = Field(default_factory=list)
    estimated_risk: str = "low"


class Plan(BaseModel):
    """Implementation plan produced by the planner."""

    summary: str
    steps: list[PlanStep]
    notes: str = ""
    research_request: ResearchRequest | None = None


class SelfReviewResponse(BaseModel):
    """Response from the self-review agent."""

    summary: str
    issues: list[str] = Field(default_factory=list)
    needs_rework: bool = False
    research_request: ResearchRequest | None = None


class ReporterResponse(BaseModel):
    """Response from the reporter agent."""

    pr_title: str
    pr_description: str
    corporate_report: str
    commit_message: str = ""


class FileOperation(BaseModel):
    """A single file operation produced by the maker agent."""

    path: str
    operation: str = Field(..., pattern="^(create|edit|delete)$")
    content: str | None = None
    old_string: str | None = None
    explanation: str = ""


class MakerResponse(BaseModel):
    """Response from the maker agent containing file operations."""

    summary: str
    operations: list[FileOperation] = Field(default_factory=list)
    test_commands: list[list[str]] = Field(default_factory=list)
    research_request: ResearchRequest | None = None



class PrioritizedTask(BaseModel):
    """A single task in the LLM-evaluated execution order."""

    task_id: str
    reason: str = ""


class PrioritizationResult(BaseModel):
    """LLM output: ordered list of task IDs with justifications."""

    ordered_tasks: list[PrioritizedTask]
    notes: str = ""
