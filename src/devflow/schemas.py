"""Pydantic schemas for structured LLM outputs used by graph nodes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ApprovalResponse(BaseModel):
    """Response from the plan approval agent."""

    approved: bool
    reason: str
    requested_changes: list[str] = Field(default_factory=list)


class SelfReviewResponse(BaseModel):
    """Response from the self-review agent."""

    summary: str
    issues: list[str] = Field(default_factory=list)
    needs_rework: bool = False


class ReporterResponse(BaseModel):
    """Response from the reporter agent."""

    pr_title: str
    pr_description: str
    corporate_report: str


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
