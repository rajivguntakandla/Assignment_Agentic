from __future__ import annotations

import json

from ..gates import waves
from ..llm import schema_instructions
from ..models import Plan, Task
from ..resilience import OutputValidationError
from .base import Agent, AgentContext, AgentResult


class PlanningAgent(Agent):
    """Decomposes the spec into a dependency-ordered task graph (waves of independent tasks)."""

    name = "planning"

    def run(self, ctx: AgentContext) -> AgentResult:
        spec = ctx.inputs["requirements"]
        previous = ctx.state.get("artifacts", {}).get("planning")
        user = (
            f"Specification:\n{json.dumps(spec, indent=2)}\n\n"
            + (f"Previous plan (superseded because upstream changed):\n{json.dumps(previous, indent=2)}\n\n"
               if previous else "")
            + "Decompose into tasks. Each task: id (T1, T2, ...), kind (code|test|migration|docs|config), "
            "depends_on (task ids), requirement_ids it satisfies, and the repository-relative files it touches. "
            "Every FR must be covered by at least one task; include at least one test task that depends on the "
            "code it tests. Leave waves empty (computed by the orchestrator).\n\n" + schema_instructions(Plan)
        )

        def validate(plan: Plan) -> None:
            tasks = [t.model_dump() for t in plan.tasks]
            if not waves(tasks):
                raise OutputValidationError("task dependencies contain a cycle or unknown ids")
            missing = {fr["id"] for fr in spec["functional_requirements"]} - {
                r for t in plan.tasks for r in t.requirement_ids}
            if missing:
                raise OutputValidationError(f"requirements not covered by any task: {sorted(missing)}")

        plan, provider = self.llm_or_fallback(ctx, system="You are the delivery planner.", user=user, schema=Plan,
                                              validate=validate, fallback=lambda: self._deterministic(spec))
        assert isinstance(plan, Plan)
        plan.waves = waves([t.model_dump() for t in plan.tasks])
        plan.revision = (previous or {}).get("revision", 0) + 1
        decisions = [(f"Plan r{plan.revision}: {len(plan.tasks)} tasks in {len(plan.waves)} waves",
                      plan.rationale or "dependency-ordered decomposition")]
        return AgentResult(plan.model_dump(mode="json"), decisions, provider)

    @staticmethod
    def _deterministic(spec: dict) -> Plan:
        tasks: list[Task] = []
        for i, fr in enumerate(spec["functional_requirements"], start=1):
            tasks.append(Task(id=f"T{i}", title=f"Implement {fr['id']}", kind="code", requirement_ids=[fr["id"]]))
        n = len(tasks)
        tasks.append(Task(id=f"T{n + 1}", title="Automated tests for all FRs", kind="test",
                          depends_on=[t.id for t in tasks],
                          requirement_ids=[fr["id"] for fr in spec["functional_requirements"]]))
        tasks.append(Task(id=f"T{n + 2}", title="Document the change", kind="docs", depends_on=[f"T{n + 1}"]))
        return Plan(tasks=tasks, rationale="Deterministic fallback: one code task per FR, then tests, then docs")
