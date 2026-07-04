"""Helpers for calling LLMs with structured JSON output."""

from __future__ import annotations

import json
import logging
import re
from typing import TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _extract_json(text: str) -> str:
    """Extract JSON from a markdown code block or the raw string."""
    # Try fenced code block
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()

    # Try first { ... } block
    match = re.search(r"(\{[\s\S]*\})", text)
    if match:
        return match.group(1).strip()

    return text.strip()


def _parse_json(text: str, model_cls: type[T]) -> T:
    """Parse text into a Pydantic model."""
    raw = _extract_json(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse JSON from model output: {exc}\nRaw: {raw}") from exc
    try:
        return model_cls(**data)
    except ValidationError as exc:
        raise ValueError(f"Model output did not match schema: {exc}\nData: {data}") from exc


def call_structured(
    llm: BaseChatModel,
    system_prompt: str,
    user_prompt: str,
    output_schema: type[T],
) -> T:
    """Call an LLM and parse the result into a Pydantic model."""
    messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    # Try native structured output first if available
    try:
        structured = llm.with_structured_output(output_schema)
        result = structured.invoke(messages)
        if isinstance(result, output_schema):
            return result
    except Exception as exc:
        logger.debug("Native structured output failed, falling back to JSON parsing: %s", exc)

    response = llm.invoke(messages)
    content = response.content
    if not isinstance(content, str):
        raise ValueError(f"Unexpected LLM response type: {type(content)}")
    return _parse_json(content, output_schema)
