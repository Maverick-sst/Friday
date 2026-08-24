"""Buyer agent core types.

The brain is swappable: a deterministic ScriptedBrain powers zero-dependency
demos; an OpenAI-compatible tool-calling brain activates when API keys exist.
Brains only *propose* actions - every financial gate stays server-side.
"""

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentEvent(BaseModel):
    """One line of the agent console stream."""

    type: Literal["status", "tool_call", "tool_result", "final", "error"]
    tool: str | None = None
    label: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolContext:
    """Everything a tool implementation may touch. Nothing else."""

    def __init__(self, db, session_row, merchant):
        self.db = db
        self.session = session_row
        self.merchant = merchant
        self.memory: dict[str, Any] = {}


ToolFn = Callable[[ToolContext, dict], dict]


class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict,
        fn: ToolFn,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters  # JSON-schema style
        self.fn = fn

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
