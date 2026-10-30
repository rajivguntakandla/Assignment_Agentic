"""Entry and exit gates. Entry gates protect a stage from running on incomplete inputs; exit gates decide
whether its output may flow downstream. Gates are pure functions of state, so they are trivially testable."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .dag import STAGES


@dataclass
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)

    @staticmethod
    def check(conditions: list[tuple[bool, str]]) -> GateResult:
        failed = [msg for ok, msg in conditions if not ok]
        return GateResult(not failed, failed)


def entry_gate(stage: str, state: dict[str, Any]) -> GateResult:
    artifacts = state.get("artifacts", {})
    missing = [dep for dep in STAGES[stage].consumes
               if STAGES[dep].kind == "agent" and dep not in artifacts]
    conditions = [(not missing, f"missing upstream artifacts: {missing}")]
    stages = state.get("stages", {})
    stale = [dep for dep in STAGES[stage].consumes if stages.get(dep, {}).get("status") == "stale"]
    conditions.append((not stale, f"upstream artifacts are stale: {stale}"))
    if stage == "planning":
        req = artifacts.get("requirements", {})
        conditions.append((not req.get("needs_clarification"), "requirements still need clarification"))
    if stage == "implementation":
        design = artifacts.get("design", {})
        approved = state.get("approvals", {}).get("design_approval", {}).get("decision") == "approve"
        conditions.append((not design.get("requires_approval") or approved,
                           "high-impact design has not been approved by a human"))
    if stage == "release":
        decision = state.get("approvals", {}).get("release_approval", {}).get("decision")
        conditions.append((decision == "approve", "release has not been approved by a human"))
    return GateResult.check(conditions)


def exit_gate(stage: str, artifact: dict[str, Any], state: dict[str, Any]) -> GateResult:
    fn = _EXIT.get(stage)
    return fn(artifact, state) if fn else GateResult(True)


def _requirements(a: dict[str, Any], _: dict[str, Any]) -> GateResult:
    frs = a.get("functional_requirements", [])
    return GateResult.check([
        (bool(frs), "no functional requirements"),
        (all(fr.get("acceptance_criteria") for fr in frs), "a requirement lacks acceptance criteria"),
        (len({fr["id"] for fr in frs}) == len(frs), "duplicate requirement ids"),
    ])


def _planning(a: dict[str, Any], state: dict[str, Any]) -> GateResult:
    tasks = a.get("tasks", [])
    ids = {t["id"] for t in tasks}
    fr_ids = {fr["id"] for fr in state["artifacts"]["requirements"]["functional_requirements"]}
    covered = {r for t in tasks for r in t.get("requirement_ids", [])}
    return GateResult.check([
        (len(ids) == len(tasks), "duplicate task ids"),
        (all(d in ids for t in tasks for d in t.get("depends_on", [])), "task depends on an unknown task"),
        (is_acyclic(tasks), "task graph has a cycle"),
        (fr_ids <= covered, f"requirements without tasks: {sorted(fr_ids - covered)}"),
        (any(t["kind"] == "test" for t in tasks), "plan has no test task"),
    ])


def _design(a: dict[str, Any], _: dict[str, Any]) -> GateResult:
    return GateResult.check([
        (all(d.get("rationale") for d in a.get("decisions", [])), "a design decision lacks rationale"),
        (bool(a.get("planned_files")), "design does not name the files it will change"),
        (a.get("risk_level") in {"low", "medium", "high"}, "risk not classified"),
    ])


def _implementation(a: dict[str, Any], _: dict[str, Any]) -> GateResult:
    return GateResult.check([
        (a.get("blocking_findings", 1) == 0, "blocking policy findings remain"),
        (not a.get("untraced_tasks"), f"plan tasks without changes: {a.get('untraced_tasks')}"),
        (bool(a.get("files_changed")), "no files changed"),
    ])


def _verification(a: dict[str, Any], _: dict[str, Any]) -> GateResult:
    return GateResult.check([
        (a.get("exit_code") == 0, f"build exited with {a.get('exit_code')}"),
        (a.get("tests", 0) > 0, "no tests executed"),
        (a.get("failures", 0) + a.get("errors", 0) == 0, f"{a.get('failures', 0)} failures, "
                                                         f"{a.get('errors', 0)} errors"),
    ])


def _security(a: dict[str, Any], _: dict[str, Any]) -> GateResult:
    return GateResult.check([(not a.get("blocking"), "blocking security/compliance findings")])


def _documentation(a: dict[str, Any], state: dict[str, Any]) -> GateResult:
    doc = a.get("feature_doc", "")
    fr_ids = [fr["id"] for fr in state["artifacts"]["requirements"]["functional_requirements"]]
    return GateResult.check([
        (len(doc) >= 200, "feature documentation is too thin"),
        (all(fr in doc for fr in fr_ids), "feature doc does not reference every requirement id"),
        (bool(a.get("changelog_entry")), "changelog entry missing"),
    ])


def _release(a: dict[str, Any], _: dict[str, Any]) -> GateResult:
    return GateResult.check([(bool(a.get("patch_path")), "no patch produced"),
                             (bool(a.get("summary_path")), "engineering summary missing")])


_EXIT = {"requirements": _requirements, "planning": _planning, "design": _design,
         "implementation": _implementation, "verification": _verification, "security_review": _security,
         "documentation": _documentation, "release": _release}


def is_acyclic(tasks: list[dict[str, Any]]) -> bool:
    return len(waves(tasks)) > 0 or not tasks


def waves(tasks: list[dict[str, Any]]) -> list[list[str]]:
    """Group tasks into dependency levels; tasks within one wave are independent. Empty on cycles."""
    deps = {t["id"]: set(t.get("depends_on", [])) & {x["id"] for x in tasks} for t in tasks}
    indeg = {k: len(v) for k, v in deps.items()}
    level = deque(sorted(k for k, d in indeg.items() if d == 0))
    out: list[list[str]] = []
    done = 0
    while level:
        current = sorted(level)
        out.append(current)
        done += len(current)
        level = deque()
        for tid, d in deps.items():
            if d & set(current):
                indeg[tid] -= len(d & set(current))
                if indeg[tid] == 0:
                    level.append(tid)
    return out if done == len(tasks) else []
