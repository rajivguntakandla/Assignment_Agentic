from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..resilience import OutputValidationError, PermanentError, TransientError

if TYPE_CHECKING:
    from ..runtime import Runtime


class AgentFailure(Exception):
    """The agent could not produce an acceptable artifact within its bounds."""


@dataclass
class AgentContext:
    rt: Runtime
    stage: str
    inputs: dict[str, dict[str, Any]]
    state: dict[str, Any]
    span_id: str
    variant: int = 1
    feedback: list[str] = field(default_factory=list)


@dataclass
class AgentResult:
    artifact: dict[str, Any]
    decisions: list[tuple[str, str]] = field(default_factory=list)  # (summary, rationale)
    provider: str = "deterministic"
    notes: list[str] = field(default_factory=list)


SYSTEM_PREAMBLE = (
    "You are one agent inside a governed software-delivery pipeline for a Java 21 / Spring Boot 3.5 URL "
    "shortener service. Your output is machine-validated and reviewed by humans before release. Be precise, "
    "do not invent files or APIs you were not shown, and prefer the smallest change that satisfies the "
    "acceptance criteria. "
)


class Agent:
    name = "agent"

    def run(self, ctx: AgentContext) -> AgentResult:  # pragma: no cover - interface
        raise NotImplementedError

    def llm_or_fallback(self, ctx: AgentContext, *, system: str, user: str, schema: type[BaseModel],
                        fallback: Any = None, validate: Any = None) -> tuple[BaseModel, str]:
        """Ask the LLM chain; if every provider fails, use the deterministic fallback when one is safe."""
        try:
            return ctx.rt.llm.structured(agent=self.name, stage=ctx.stage, variant=ctx.variant,
                                         system=SYSTEM_PREAMBLE + system, user=user, schema=schema,
                                         validate=validate, span_id=ctx.span_id)
        except (PermanentError, TransientError, OutputValidationError) as exc:
            if fallback is None:
                raise AgentFailure(f"{self.name}: no LLM provider succeeded and no safe fallback exists: {exc}")\
                    from exc
            ctx.rt.audit.record("fallback", stage=ctx.stage, parent_span_id=ctx.span_id, data={
                "scope": "agent", "agent": self.name, "from": "llm", "to": "deterministic",
                "reason": str(exc)[:300]})
            return fallback(), "deterministic-fallback"
