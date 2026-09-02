"""Execution context handed to mission handlers and agent runs.

Carries everything bounded work needs: config, queue (for cancellation),
budget counters, and a cancel-check coroutine. Nothing here is process-global;
every concurrent execution gets its own context instance.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.core.config import Settings, get_settings
from app.engine.queue import JobQueue


class BudgetExhausted(Exception):
    """Raised when the run/mission budget forbids further work."""


class MissionCancelled(Exception):
    """Raised cooperatively by handlers observing the mission cancel signal.

    Distinct from asyncio.CancelledError so the executor can convert this
    into a clean CANCELLED terminal state without unwinding the worker task.
    """


@dataclass
class RunBudget:
    """Mutable counters for one agent run. Enforced at every tool call."""

    max_tool_calls: int
    tool_calls_used: int = 0
    llm_calls_used: int = 0

    def consume_tool_call(self) -> None:
        if self.tool_calls_used >= self.max_tool_calls:
            raise BudgetExhausted(
                f"tool-call budget exhausted ({self.tool_calls_used}/{self.max_tool_calls})"
            )
        self.tool_calls_used += 1


@dataclass
class MissionContext:
    """Per-execution context; never shared between concurrent missions."""

    mission_id: str
    merchant_id: str
    objective: str
    mission_type: str
    settings: Settings
    queue: JobQueue | None = None
    check_cancel: Callable[[], Awaitable[bool]] | None = None
    deadline: float | None = None  # event-loop time
    artifacts: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_mission_row(mission: Any) -> "MissionContext":
        return MissionContext(
            mission_id=mission.id,
            merchant_id=mission.merchant_id,
            objective=mission.objective,
            mission_type=mission.mission_type,
            settings=get_settings(),
        )

    async def cancelled(self) -> bool:
        if self.check_cancel is None:
            return False
        try:
            return await self.check_cancel()
        except Exception:
            return False

    def remaining_seconds(self) -> float | None:
        if self.deadline is None:
            return None
        return self.deadline - time.monotonic()

    async def ensure_not_cancelled(self) -> None:
        if await self.cancelled():
            raise MissionCancelled()


HandlerResult = dict[str, Any]
MissionHandler = Callable[[MissionContext], Awaitable[HandlerResult]]
_HANDLERS: dict[str, MissionHandler] = {}


def register_handler(mission_type: str, handler: MissionHandler) -> None:
    _HANDLERS[mission_type] = handler


def get_handler(mission_type: str) -> MissionHandler | None:
    return _HANDLERS.get(mission_type)


def registered_types() -> list[str]:
    return sorted(_HANDLERS)
