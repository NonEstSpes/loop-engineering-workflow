"""Unit tests for the Telegram human-in-the-loop bridge."""

from __future__ import annotations

from devflow.schemas import Plan, PlanStep
from devflow.state import Task
from devflow.telegram_bridge import TelegramBridge


class FakeTelegramChannel:
    """Records sends and returns canned callback/text responses."""

    def __init__(self) -> None:
        self.sends: list[str] = []
        self.keyboards: list[list[list[dict[str, str]]]] = []
        self.callback_result: str = "approve"
        self.text_results: list[str] = []
        self._text_index = 0

    def send_with_inline_keyboard(
        self,
        message: str,
        buttons: list[list[dict[str, str]]],
        *,
        parse_mode: str | None = "Markdown",
    ) -> int:
        self.sends.append(message)
        self.keyboards.append(buttons)
        return 1  # message_id

    def wait_for_callback_query(self, message_id: int, *, timeout: float = 300) -> str:
        return self.callback_result

    def wait_for_text_reply(self, *, timeout: float = 300) -> str:
        text = self.text_results[self._text_index]
        self._text_index += 1
        return text

    def send(self, message: str, *, parse_mode: str | None = "Markdown") -> str:
        self.sends.append(message)
        return "telegram://message/0"

    def close(self) -> None:
        pass


def _task() -> Task:
    return Task(id="T-1", title="Add feature", description="desc")


def _plan() -> Plan:
    return Plan(
        summary="Implement feature",
        steps=[PlanStep(id="s1", description="step one", files_to_touch=["a.py"])],
    )


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


def test_approve_path() -> None:
    channel = FakeTelegramChannel()
    channel.callback_result = "approve"
    bridge = TelegramBridge(channel)

    result = bridge.request_plan_approval(_task(), _plan())

    assert result == {
        "approved": True,
        "reason": "Approved via Telegram",
        "requested_changes": [],
    }
    # One message sent: the plan prompt with inline keyboard.
    assert len(channel.sends) == 1
    assert "Add feature" in channel.sends[0]
    # Three buttons rows.
    assert len(channel.keyboards[0]) == 3


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


def test_reject_path_collects_reason() -> None:
    channel = FakeTelegramChannel()
    channel.callback_result = "reject"
    channel.text_results = ["Plan too risky"]
    bridge = TelegramBridge(channel)

    result = bridge.request_plan_approval(_task(), _plan())

    assert result["approved"] is False
    assert result["reason"] == "Plan too risky"
    assert result["requested_changes"] == []
    # Two messages: the prompt + "describe the reason" prompt.
    assert len(channel.sends) == 2
    assert "причину" in channel.sends[1]


# ---------------------------------------------------------------------------
# changes
# ---------------------------------------------------------------------------


def test_changes_path_collects_requested_changes() -> None:
    channel = FakeTelegramChannel()
    channel.callback_result = "changes"
    channel.text_results = ["Add tests\n- Refactor module\nHandle errors"]
    bridge = TelegramBridge(channel)

    result = bridge.request_plan_approval(_task(), _plan())

    assert result["approved"] is False
    assert result["reason"] == "Changes requested via Telegram"
    assert result["requested_changes"] == ["Add tests", "Refactor module", "Handle errors"]


# ---------------------------------------------------------------------------
# unknown callback
# ---------------------------------------------------------------------------


def test_unknown_callback_treated_as_rejection() -> None:
    channel = FakeTelegramChannel()
    channel.callback_result = "weird"
    bridge = TelegramBridge(channel)

    result = bridge.request_plan_approval(_task(), _plan())

    assert result["approved"] is False
    assert "weird" in result["reason"]


# ---------------------------------------------------------------------------
# message formatting
# ---------------------------------------------------------------------------


def test_message_contains_task_and_plan() -> None:
    channel = FakeTelegramChannel()
    channel.callback_result = "approve"
    bridge = TelegramBridge(channel)

    bridge.request_plan_approval(_task(), _plan())

    msg = channel.sends[0]
    assert "T-1" in msg
    assert "Add feature" in msg
    assert "Implement feature" in msg
    assert "step one" in msg
    assert "a.py" in msg
