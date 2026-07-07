"""Shared pytest fixtures for the DevFlow test suite."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel

from devflow.config import (
    AgentConfig,
    Config,
    ProviderConfig,
    ResearchSourcesConfig,
    WorkflowConfig,
)
from devflow.mcp.mock import MockTaskSource
from devflow.schemas import (
    ApprovalResponse,
    MakerResponse,
    PlanStep,
    ReporterResponse,
    SelfReviewResponse,
)
from devflow.state import CheckerReport, CheckerVerdict, Plan


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for the test."""
    return tmp_path


@pytest.fixture
def mock_task_source() -> MockTaskSource:
    """Return a mock task source with canned tasks."""
    return MockTaskSource({})


@pytest.fixture
def mock_config(temp_dir: Path) -> Config:
    """Create a minimal valid Config object in a temp directory."""
    agents_dir = temp_dir / "agents"
    agents_dir.mkdir()

    workflow = WorkflowConfig(
        task_source="mock",
        max_rework_iterations=3,
        human_in_the_loop=False,
        default_branch="main",
        pr_target_branch="main",
        corporate_report_channels=["console"],
    )
    providers = {
        "mock": ProviderConfig(name="mock"),
        "openai": ProviderConfig(name="openai", api_key="test-key"),
    }
    agents = {
        name: AgentConfig(
            name=name,
            provider="mock",
            model="mock-model",
            system_prompt=f"You are the {name} agent.",
            auto_approve=(name == "plan_approval"),
        )
        for name in [
            "orchestrator",
            "planner",
            "plan_approval",
            "maker",
            "self_review",
            "checker_a",
            "checker_b",
            "checker_c",
            "reporter",
            "research",
        ]
    }

    # Write markdown agent configs so load_config would work too
    for name, cfg in agents.items():
        (agents_dir / f"{name}.md").write_text(
            f"---\nname: {name}\nprovider: mock\nmodel: mock-model\nauto_approve: "
            f"{str(cfg.auto_approve).lower()}\n---\n\n{cfg.system_prompt}\n",
            encoding="utf-8",
        )

    (temp_dir / "workflow.yaml").write_text(
        "task_source: mock\nmax_rework_iterations: 3\nhuman_in_the_loop: false\n"
        "default_branch: main\npr_target_branch: main\n"
        "corporate_report_channels:\n  - console\n",
        encoding="utf-8",
    )
    (temp_dir / "providers.yaml").write_text(
        "mock:\n  pass_through: true\nopenai:\n  api_key: test-key\n",
        encoding="utf-8",
    )

    return Config(
        workflow=workflow,
        providers=providers,
        agents=agents,
        research_sources=ResearchSourcesConfig(),
    )


class FakeStructuredRunnable:
    """A runnable that returns a preconfigured Pydantic object."""

    def __init__(self, output: BaseModel) -> None:
        self._output = output

    def invoke(self, *args: Any, **kwargs: Any) -> BaseModel:
        return self._output

    async def ainvoke(self, *args: Any, **kwargs: Any) -> BaseModel:
        return self._output


class FakeChatModel(BaseChatModel):
    """A fake LLM that returns configured structured outputs by schema."""

    outputs: dict[type[BaseModel], BaseModel]

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=messages[-1])])

    @property
    def _llm_type(self) -> str:
        return "fake"

    def with_structured_output(self, schema: type[BaseModel], **kwargs: Any) -> Any:
        output = self.outputs.get(schema)
        if output is None:
            raise ValueError(f"No fake output configured for schema {schema.__name__}")
        return FakeStructuredRunnable(output)


def _default_fake_outputs() -> dict[type[BaseModel], BaseModel]:
    """Return sensible structured outputs for a successful workflow pass."""
    return {
        Plan: Plan(
            summary="Create a hello endpoint",
            steps=[
                PlanStep(
                    id="step-1",
                    description="Add a hello.py file with a greeting function.",
                    files_to_touch=["hello.py"],
                    tests_to_add=["tests/test_hello.py"],
                    estimated_risk="low",
                ),
            ],
        ),
        ApprovalResponse: ApprovalResponse(approved=True, reason="Looks good"),
        MakerResponse: MakerResponse(
            summary="Added hello.py",
            operations=[
                {
                    "path": "hello.py",
                    "operation": "create",
                    "content": "def hello() -> str:\n    return 'Hello, world!'\n",
                },
            ],
            test_commands=[],
        ),
        SelfReviewResponse: SelfReviewResponse(
            summary="Implementation matches the plan.",
            issues=[],
            needs_rework=False,
        ),
        CheckerReport: CheckerReport(
            agent_name="checker",
            verdict=CheckerVerdict.APPROVE,
            summary="No issues found.",
        ),
        ReporterResponse: ReporterResponse(
            pr_title="Add hello endpoint",
            pr_description="Implements the hello endpoint.",
            corporate_report="All checkers approved.",
        ),
    }


@pytest.fixture
def fake_llm() -> FakeChatModel:
    """Return a fake LLM with default successful outputs."""
    return FakeChatModel(outputs=_default_fake_outputs())


@pytest.fixture
def fake_llm_factory(fake_llm: FakeChatModel, monkeypatch: pytest.MonkeyPatch) -> FakeChatModel:
    """Patch the LLM factory so every node receives the fake LLM."""
    import devflow.llm_factory as llm_factory

    def _fake_build_impl(agent_cfg: AgentConfig, app_cfg: Config) -> BaseChatModel:
        return fake_llm

    monkeypatch.setattr(llm_factory, "_build_llm_impl", _fake_build_impl)
    return fake_llm
