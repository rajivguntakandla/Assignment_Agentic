from __future__ import annotations

import json

from ..llm import schema_instructions
from ..models import ChangeSet, PolicyFinding
from ..policy import count_tests, summarize
from ..resilience import OutputValidationError
from .base import Agent, AgentContext, AgentFailure, AgentResult

MAX_CONTEXT_FILE_CHARS = 12_000


class ImplementationAgent(Agent):
    """Generates a change set and applies it inside the sandbox under change-control policy.

    Sequence per attempt: generate -> pre-apply path policy -> snapshot -> apply -> post-apply diff policy.
    Any blocking finding rolls the workspace back to the snapshot and feeds the findings back to the model.
    There is deliberately no deterministic fallback: the orchestrator never fabricates production code.
    """

    name = "implementation"

    def run(self, ctx: AgentContext) -> AgentResult:
        rt = ctx.rt
        spec, plan, design = ctx.inputs["requirements"], ctx.inputs["planning"], ctx.inputs["design"]
        existing = rt.workspace.files()
        context_files = sorted(set(design["planned_files"]) | set(design["impact"]["seed_files"]))
        sources = {f: rt.workspace.read(f)[:MAX_CONTEXT_FILE_CHARS] for f in context_files if f in existing}
        tests_before = count_tests(rt.workspace.baseline)
        feedback = list(ctx.feedback)
        snapshot = f"pre-implementation-r{ctx.variant}"
        snap_hash = rt.workspace.snapshot(snapshot)

        for attempt in range(1, rt.settings.stage_max_attempts + 1):
            user = self._prompt(spec, plan, design, sources, feedback)
            changeset, provider = self.llm_or_fallback(
                ctx, system="You are a senior Java engineer.", user=user, schema=ChangeSet,
                validate=lambda cs: self._validate_trace(cs, plan))
            assert isinstance(changeset, ChangeSet)

            findings = rt.policy.check_paths(changeset.changes, existing)
            if not self._blocking(findings):
                try:
                    rt.workspace.apply(changeset)
                except OutputValidationError as exc:
                    findings.append(PolicyFinding(rule="APPLY-001", category="change_control", severity="block",
                                                  message=str(exc)))
                else:
                    patch, diffs = rt.workspace.diff()
                    findings += rt.policy.check_diff(diffs, design["planned_files"], tests_before,
                                                     count_tests(rt.workspace.root))
            rt.audit.record("policy_evaluated", stage=ctx.stage, parent_span_id=ctx.span_id, data={
                **summarize(findings), "attempt": attempt, "findings": [f.model_dump() for f in findings]})

            if not self._blocking(findings):
                patch_path = rt.save_artifact(f"implementation-r{ctx.variant}.patch", patch)
                rt.save_artifact(f"changeset-r{ctx.variant}.json", changeset.model_dump(mode="json"))
                tasks_by_wave = {t: i for i, wave in enumerate(plan["waves"], start=1) for t in wave}
                return AgentResult({
                    "summary": changeset.summary,
                    "files_changed": sorted({c.path for c in changeset.changes}),
                    "tasks_covered": sorted({c.task_id for c in changeset.changes}),
                    "applied_waves": sorted({tasks_by_wave.get(c.task_id, 0) for c in changeset.changes}),
                    "untraced_tasks": self._untraced(changeset, plan),
                    "lines_added": sum(len(d.added_lines) for d in diffs),
                    "lines_removed": sum(d.removed_lines for d in diffs),
                    "tests_before": tests_before, "tests_after": count_tests(rt.workspace.root),
                    "policy_findings": [f.model_dump() for f in findings],
                    "blocking_findings": 0,
                    "snapshot": snapshot, "snapshot_hash": snap_hash,
                    "workspace_hash": rt.workspace.tree_hash(rt.workspace.root),
                    "patch_path": str(patch_path), "round": ctx.variant,
                }, [(f"Change set r{ctx.variant}: {len(changeset.changes)} file(s)", changeset.summary)], provider)

            restored = rt.workspace.restore(snapshot)
            rt.audit.record("rollback", stage=ctx.stage, parent_span_id=ctx.span_id, data={
                "reason": "blocking policy findings", "snapshot": snapshot, "restored_hash": restored,
                "attempt": attempt})
            feedback = feedback + [f"{f.rule} {f.path or ''}:{f.line or ''} {f.message}"
                                   for f in findings if f.severity == "block"]
            if attempt < rt.settings.stage_max_attempts:
                rt.audit.record("retry", stage=ctx.stage, parent_span_id=ctx.span_id, data={
                    "scope": "stage", "attempt": attempt, "reason": "policy violations fed back to agent"})
        raise AgentFailure("implementation could not satisfy policy within the attempt budget")

    @staticmethod
    def _blocking(findings: list[PolicyFinding]) -> bool:
        return any(f.severity == "block" for f in findings)

    @staticmethod
    def _untraced(cs: ChangeSet, plan: dict) -> list[str]:
        needed = {t["id"] for t in plan["tasks"] if t["kind"] in ("code", "test", "migration", "config")}
        return sorted(needed - {c.task_id for c in cs.changes})

    def _validate_trace(self, cs: ChangeSet, plan: dict) -> None:
        known = {t["id"] for t in plan["tasks"]}
        unknown = sorted({c.task_id for c in cs.changes} - known)
        if unknown:
            raise OutputValidationError(f"changes reference unknown task ids {unknown}")
        untraced = self._untraced(cs, plan)
        if untraced:
            raise OutputValidationError(f"plan tasks without any change: {untraced}")

    @staticmethod
    def _prompt(spec: dict, plan: dict, design: dict, sources: dict[str, str], feedback: list[str]) -> str:
        files = "\n\n".join(f"=== {path} ===\n{body}" for path, body in sources.items())
        return (
            f"Specification:\n{json.dumps(spec, indent=2)}\n\nPlan:\n{json.dumps(plan, indent=2)}\n\n"
            f"Approved design:\n{json.dumps({k: design[k] for k in ('summary', 'decisions', 'api_changes', 'schema_changes', 'planned_files')}, indent=2)}\n\n"  # noqa: E501
            f"Current source of relevant files:\n{files}\n\n"
            + ("Feedback from the previous attempt (fix ALL of these):\n- " + "\n- ".join(feedback) + "\n\n"
               if feedback else "")
            + "Produce a change set. For new files use action=create with full content. For existing files use "
            "action=modify with edits whose `search` text appears EXACTLY ONCE in the current file. Tag every "
            "change with the task_id it implements. Include JUnit 5 tests (MockMvc for HTTP behaviour). Never "
            "modify existing migration files; add a new V<n>__*.sql instead.\n\n" + schema_instructions(ChangeSet)
        )


__all__ = ["AgentFailure", "ImplementationAgent"]
