"""Mock LLM for dry-run workflow execution without external API calls."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel

from devflow.schemas import (
    ApprovalResponse,
    MakerResponse,
    Plan,
    PlanStep,
    ReporterResponse,
    SelfReviewResponse,
)
from devflow.state import CheckerReport, CheckerVerdict


def _default_mock_outputs() -> dict[type[BaseModel], BaseModel]:
    """Return deterministic structured outputs for every workflow schema."""
    return {
        Plan: Plan(
            summary="Mock plan: verify repository structure and add a small improvement",
            steps=[
                PlanStep(
                    id="step-1",
                    description="Inspect the repository structure.",
                    files_to_touch=[],
                    tests_to_add=[],
                    estimated_risk="low",
                ),
                PlanStep(
                    id="step-2",
                    description="Add a minor documentation note or refactor.",
                    files_to_touch=["README.md"],
                    tests_to_add=[],
                    estimated_risk="low",
                ),
            ],
            notes="This is a mock plan produced for local verification.",
        ),
        ApprovalResponse: ApprovalResponse(
            approved=True,
            reason="Mock approval: plan looks acceptable.",
        ),
        MakerResponse: MakerResponse(
            summary="Mock implementation: added a verification marker.",
            operations=[
                {
                    "path": "devflow-mock-note.md",
                    "operation": "create",
                    "content": "# DevFlow mock run\n\nThis file was created during a mock workflow execution.\n",
                }
            ],
            test_commands=[],
        ),
        SelfReviewResponse: SelfReviewResponse(
            summary="Mock self-review: implementation matches the mock plan.",
            issues=[],
            needs_rework=False,
        ),
        CheckerReport: CheckerReport(
            agent_name="mock-checker",
            verdict=CheckerVerdict.APPROVE,
            summary="Mock checker: no issues found.",
        ),
        ReporterResponse: ReporterResponse(
            pr_title="Mock PR: devflow verification run",
            pr_description="This is a mock PR generated for workflow verification.",
            corporate_report="Mock corporate report: workflow completed successfully.",
        ),
    }


class _MockStructuredRunnable:
    """Runnable returned by MockChatModel.with_structured_output."""

    def __init__(self, output: BaseModel) -> None:
        self._output = output

    def invoke(self, *args: Any, **kwargs: Any) -> BaseModel:
        return self._output

    async def ainvoke(self, *args: Any, **kwargs: Any) -> BaseModel:
        return self._output


class MockChatModel(BaseChatModel):
    """Deterministic fake chat model for dry-run / mock-data execution."""

    outputs: dict[type[BaseModel], BaseModel]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(outputs=_default_mock_outputs(), **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        content = "Mock LLM response. Use with_structured_output for schema-aware output."
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    @property
    def _llm_type(self) -> str:
        return "mock"

    def with_structured_output(self, schema: type[BaseModel], **kwargs: Any) -> Any:
        output = self.outputs.get(schema)
        if output is None:
            raise ValueError(f"MockChatModel has no configured output for schema {schema.__name__}")
        return _MockStructuredRunnable(output)

    def model_dump_json(self, **kwargs: Any) -> str:
        return json.dumps({"outputs": {k.__name__: v.model_dump() for k, v in self.outputs.items()}})
