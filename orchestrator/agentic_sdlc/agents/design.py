from __future__ import annotations

import json
from pathlib import Path

from ..codebase import CodebaseIndex
from ..llm import schema_instructions
from ..models import Design, DesignDecision
from ..resilience import OutputValidationError
from .base import Agent, AgentContext, AgentResult


class DesignAgent(Agent):
    """Brownfield reasoning + architecture decisions.

    Impact analysis and risk classification are deterministic (static analysis + policy), so an LLM can propose a
    design but can never under-state its blast radius or talk its way out of a human approval.
    """

    name = "design"

    def run(self, ctx: AgentContext) -> AgentResult:
        spec, plan = ctx.inputs["requirements"], ctx.inputs["planning"]
        index = CodebaseIndex(ctx.rt.workspace.baseline)
        seeds = index.seeds_for_terms(spec.get("domain_terms", []))
        preliminary = index.impact(seeds)
        existing = ctx.rt.workspace.files()

        user = (
            f"Specification:\n{json.dumps(spec, indent=2)}\n\nPlan:\n{json.dumps(plan, indent=2)}\n\n"
            f"Codebase structure by layer:\n{json.dumps(index.summary(), indent=2)}\n\n"
            f"Static impact analysis (seeds from domain terms):\n{preliminary.model_dump_json(indent=2)}\n\n"
            + (f"Reviewer feedback on the previous design (must be addressed):\n{ctx.feedback}\n\n"
               if ctx.feedback else "")
            + "Produce the design: decisions with rationale and rejected alternatives, API changes, schema changes "
            "(new Flyway migration files only - applied migrations are immutable), the exact repository-relative "
            "planned_files to create or modify, and risks. Leave impact/risk_level/requires_approval at defaults; "
            "the orchestrator computes them.\n\n" + schema_instructions(Design)
        )

        def validate(d: Design) -> None:
            bad = [f for f in d.planned_files if f.startswith(("/", "..")) or "/../" in f]
            if bad:
                raise OutputValidationError(f"planned_files must be repository-relative: {bad}")

        design, provider = self.llm_or_fallback(
            ctx, system="You are the software architect.", user=user, schema=Design, validate=validate,
            fallback=lambda: self._deterministic(plan, preliminary.impacted_files))
        assert isinstance(design, Design)

        # Deterministic impact over seeds + every existing file the design plans to modify.
        touched_types = [Path(f).stem for f in design.planned_files if f in existing and f.endswith(".java")]
        design.impact = index.impact(sorted(set(seeds) | set(touched_types)),
                                     extra_files=[f for f in design.planned_files if f in existing])
        design.risk_level, flags = self._classify(ctx, design, existing)
        design.impact.risk_flags = flags
        design.requires_approval = design.risk_level == "high"
        design.revision = (ctx.state.get("artifacts", {}).get("design") or {}).get("revision", 0) + 1

        decisions = [(f"{d.id}: {d.decision}", d.rationale) for d in design.decisions]
        decisions.append((f"Risk classified {design.risk_level}"
                          + (" -> human approval required" if design.requires_approval else ""),
                          "; ".join(flags) or "additive change, no sensitive surface"))
        return AgentResult(design.model_dump(mode="json"), decisions, provider)

    @staticmethod
    def _classify(ctx: AgentContext, design: Design, existing: set[str]) -> tuple[str, list[str]]:
        policy = ctx.rt.policy
        flags: list[str] = []
        planned = design.planned_files
        if design.schema_changes or any(policy.is_schema_path(f) for f in planned):
            flags.append("schema/data-model change")
        if any(policy.is_security_sensitive(f) for f in planned):
            flags.append("security-sensitive component modified")
        modified_endpoints = [f for f in planned if f in existing and "/web/" in f and f.endswith("Controller.java")]
        if modified_endpoints:
            flags.append("existing public API modified")
        if "pom.xml" in planned:
            flags.append("dependency change")
        if flags:
            return "high", flags
        return ("medium" if design.api_changes else "low"), ["additive public API"] if design.api_changes else []

    @staticmethod
    def _deterministic(plan: dict, impacted: list[str]) -> Design:
        files = sorted({f for t in plan["tasks"] for f in t.get("files", [])} | set(impacted[:4]))
        return Design(
            summary="Deterministic fallback design derived from the plan and static impact analysis",
            decisions=[DesignDecision(id="D1", decision="Follow existing layering (web -> service -> repository)",
                                      rationale="Keeps the change consistent with the established architecture",
                                      alternatives=["New module"])],
            planned_files=files, risks=["Fallback design: requires careful human review"])
