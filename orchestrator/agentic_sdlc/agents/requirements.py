from __future__ import annotations

import json
import re

from ..ambiguity import analyze
from ..llm import schema_instructions
from ..models import FunctionalRequirement, RequirementSpec
from .base import Agent, AgentContext, AgentResult


class RequirementsAgent(Agent):
    """Interprets intent and normalises it into a testable spec; escalates ambiguity to a human."""

    name = "requirements"

    def run(self, ctx: AgentContext) -> AgentResult:
        scenario = ctx.rt.scenario
        answers: dict[str, str] = ctx.state.get("clarifications") or {}
        report = analyze(scenario.requirement, answers)

        user = (
            f"Requirement kind: {scenario.kind}\nRequirement:\n{scenario.requirement}\n\n"
            f"Clarifications from the product owner (authoritative):\n{json.dumps(answers, indent=2) or '{}'}\n\n"
            f"Ambiguities detected by static analysis:\n"
            f"{json.dumps([a.model_dump() for a in report.ambiguities], indent=2)}\n\n"
            "Normalise this into a specification. Each functional requirement needs an id (FR-1, FR-2, ...) and "
            "concrete, testable acceptance criteria. List domain_terms (code-level nouns such as entity, field or "
            "class names) that locate the affected code. If anything material is still unclear and no "
            "clarification answers it, set needs_clarification=true and list it under ambiguities with a "
            "proposed default.\n\n" + schema_instructions(RequirementSpec)
        )
        spec, provider = self.llm_or_fallback(
            ctx, system="You are the requirements analyst.", user=user, schema=RequirementSpec,
            fallback=lambda: self._deterministic_spec(scenario.requirement, scenario.kind, report.ambiguities))
        assert isinstance(spec, RequirementSpec)

        # Guardrail: the deterministic analyzer can only add ambiguity, never remove it.
        if report.unresolved:
            known = {a.term for a in spec.ambiguities}
            spec.ambiguities.extend(a for a in report.ambiguities if a.term not in known)
            spec.needs_clarification = True
        if answers and not report.unresolved:
            spec.needs_clarification = False
            spec.ambiguities = []
            spec.assumptions = [*spec.assumptions, *(f"Clarified '{k}': {v}" for k, v in answers.items())]
        spec.kind = scenario.kind
        spec.revision = ctx.variant

        decisions = [(f"Normalised requirement into {len(spec.functional_requirements)} FR(s)",
                      f"intent: {spec.intent}")]
        if spec.needs_clarification:
            decisions.append((f"Escalated {len(spec.ambiguities)} ambiguity(ies) to a human",
                              "; ".join(a.term for a in spec.ambiguities)))
        return AgentResult(spec.model_dump(mode="json"), decisions, provider)

    @staticmethod
    def _deterministic_spec(text: str, kind: str, ambiguities: list) -> RequirementSpec:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if len(s.strip()) > 3]
        frs = [FunctionalRequirement(id=f"FR-{i}", text=s, acceptance_criteria=[f"Automated test demonstrates: {s}"])
               for i, s in enumerate(sentences[:6], start=1)]
        terms = sorted({w for w in re.findall(r"\b[a-z]{4,}\b", text.lower())} & {
            "link", "links", "click", "clicks", "alias", "expiry", "redirect", "domain", "domains", "stats",
            "analytics", "bulk", "rate", "limit"})
        return RequirementSpec(title=text[:60], intent=text, kind=kind, functional_requirements=frs,
                               domain_terms=terms, ambiguities=ambiguities, needs_clarification=bool(ambiguities),
                               assumptions=["Derived without an LLM: requirement sentences mapped 1:1 to FRs"])
