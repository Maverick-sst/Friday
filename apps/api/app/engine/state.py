"""Mission and agent-run lifecycle states (PRD_3 §26).

Statuses are stored as plain strings (V0 convention); these constants are the
single source of truth. Transitions are enforced defensively in the executor
via conditional UPDATE ... WHERE status IN (...), so a crashed or duplicated
worker can never corrupt state.
"""

from typing import Final

# --- Mission states ---------------------------------------------------------
MISSION_CREATED: Final = "CREATED"
MISSION_QUEUED: Final = "QUEUED"
MISSION_RUNNING: Final = "RUNNING"
MISSION_COMPLETED: Final = "COMPLETED"
MISSION_PARTIALLY_COMPLETED: Final = "PARTIALLY_COMPLETED"
MISSION_FAILED: Final = "FAILED"
MISSION_CANCELLED: Final = "CANCELLED"
MISSION_TIMED_OUT: Final = "TIMED_OUT"

MISSION_ACTIVE_STATES: Final = {MISSION_CREATED, MISSION_QUEUED, MISSION_RUNNING}
MISSION_TERMINAL_STATES: Final = {
    MISSION_COMPLETED,
    MISSION_PARTIALLY_COMPLETED,
    MISSION_FAILED,
    MISSION_CANCELLED,
    MISSION_TIMED_OUT,
}

# --- Agent-run states -------------------------------------------------------
RUN_PENDING: Final = "PENDING"
RUN_RUNNING: Final = "RUNNING"
RUN_COMPLETED: Final = "COMPLETED"
RUN_FAILED: Final = "FAILED"
RUN_CANCELLED: Final = "CANCELLED"
RUN_TIMED_OUT: Final = "TIMED_OUT"

RUN_ACTIVE_STATES: Final = {RUN_PENDING, RUN_RUNNING}
RUN_TERMINAL_STATES: Final = {RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED, RUN_TIMED_OUT}

# --- Mission types / priorities --------------------------------------------
MISSION_BASELINE: Final = "baseline"
MISSION_RECURRING: Final = "recurring"
MISSION_ON_DEMAND: Final = "on_demand"
MISSION_EXPERIMENT: Final = "experiment"

PRIORITY_LOW: Final = "low"
PRIORITY_NORMAL: Final = "normal"
PRIORITY_HIGH: Final = "high"

TERMINAL_TO_EXIT_SUMMARY = {
    MISSION_COMPLETED: "Mission completed.",
    MISSION_PARTIALLY_COMPLETED: "Budget exhausted; summarized available results.",
    MISSION_FAILED: "Mission failed.",
    MISSION_CANCELLED: "Mission cancelled.",
    MISSION_TIMED_OUT: "Mission exceeded its wall-clock budget.",
}
