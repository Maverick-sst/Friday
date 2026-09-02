"""Agent contract and shared runtime (PRD_3 §8/§27/§30).

Every specialist agent implements one contract:

    AgentContract(name, role, purpose, allowed_tools, mission_types,
                  input_schema, output_schema)

and every run returns a structured AgentRunResult that the runtime validates.
Malformed outputs are retried once through validation; persistent malformed
output fails the run with a structured error rather than poisoning downstream
evidence/findings.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError


class EvidenceItem(BaseModel):
    """Evidence emitted by an agent run (PRD_3 §16)."""

    claim: str
    source_url: str | None = None
    source_type: str = "website"
    excerpt: str | None = None
    observed_at: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    epistemic_state: Literal["fact", "inference", "speculation"] = "fact"


class FindingItem(BaseModel):
    """Finding emitted by an agent run (PRD_3 §17); must cite evidence ids."""

    title: str
    statement: str
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_refs: list[int] = []
    tags: list[str] = []


class RecommendationItem(BaseModel):
    """Recommendation emitted by an agent run (PRD_3 §18)."""

    problem: str
    why_it_matters: str | None = None
    recommendation_text: str
    expected_impact: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    finding_refs: list[int] = []
    suggested_next_mission: dict[str, Any] | None = None


class AgentRunResult(BaseModel):
    """The only valid return shape for a completed agent run (PRD_3 §8)."""

    status: Literal["completed", "partially_completed"] = "completed"
    summary: str
    findings: list[FindingItem] = []
    recommendations: list[RecommendationItem] = []
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: dict[str, Any] = {}

    # Populated by the runtime from raw tool observations; agents emit claims
    # and the runtime pairs them with captured observation metadata.
    def validated_evidence_count(self) -> int:
        return sum(1 for f in self.findings for _ in f.evidence_refs)


@dataclass
class AgentContract:
    name: str  # human name, e.g. "Market Intelligence"
    key: str  # machine key, e.g. "market"
    role: str
    purpose: str
    allowed_tools: list[str]
    mission_types: list[str]
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)


class OutputValidationError(Exception):
    """Raised when an agent's raw LLM/tool output cannot satisfy its schema."""


@dataclass
class RunContext:
    """Everything one agent run may touch. Never shared between runs."""

    mission_id: str
    run_id: str
    merchant_id: str
    agent_key: str
    objective: str
    depth: int
    parent_run_id: str | None
    contract: AgentContract
    budget_tool_calls: int
    deadline_seconds: float
    memory: Any | None = None  # MemoryStore handle (M3)
    tools: Any | None = None  # ToolRouter scoped to contract.allowlist (M3)
    llm: Any | None = None  # LLMProvider (M3)
    merchant_context: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


class BaseSpecialistAgent(ABC):
    """Common execution skeleton for all five specialists (PRD_3 §27).

    Subclasses implement `_execute` returning a validated AgentRunResult;
    this base class owns schema validation + single retry on malformed output.
    """

    contract: AgentContract

    async def execute(self, ctx: RunContext) -> AgentRunResult:
        try:
            return await self._execute(ctx)
        except (OutputValidationError, ValidationError) as first_error:
            try:
                result = await self._execute(ctx, _retry_hint=repr(first_error)[:300])
                return result
            except Exception as retry_error:
                raise OutputValidationError(
                    f"agent {ctx.agent_key} produced invalid output twice: {repr(retry_error)[:300]}"
                ) from retry_error

    @abstractmethod
    async def _execute(self, ctx: RunContext, **kwargs: Any) -> AgentRunResult: ...
