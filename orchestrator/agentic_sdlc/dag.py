"""The explicit SDLC dependency graph.

``consumes`` declares *data* dependencies: a stage may only read artifacts of the stages it consumes (least
-privilege context), and when a stage's output changes, every stage that transitively consumes it is invalidated
and re-executed (dynamic re-planning). Control flow (loops, approvals, parallel fan-out) lives in
:mod:`agentic_sdlc.engine` and is validated against this graph at build time.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class StageSpec:
    name: str
    phase: str
    consumes: tuple[str, ...] = ()
    kind: str = "agent"  # agent | checkpoint | join
    parallel_group: str | None = None
    description: str = ""
    high_impact: bool = False
    entry_gate: str = ""
    exit_gate: str = ""
    # Checkpoints whose output is fed back into an upstream stage (clarification -> requirements). Re-running
    # that upstream stage is the *consequence* of the checkpoint, so it must not invalidate it.
    feeds_back_to: tuple[str, ...] = field(default_factory=tuple)


STAGES: dict[str, StageSpec] = {s.name: s for s in [
    StageSpec("requirements", "requirements", description="Normalise intent, detect ambiguity",
              exit_gate="every FR has acceptance criteria; ambiguity resolved or escalated"),
    StageSpec("clarification", "requirements", ("requirements",), kind="checkpoint", high_impact=True,
              description="Human answers open questions", feeds_back_to=("requirements",),
              entry_gate="spec flagged needs_clarification", exit_gate="answers recorded with approver identity"),
    StageSpec("planning", "requirements", ("requirements",), description="Decompose into a task DAG",
              entry_gate="requirements approved (no open ambiguity)",
              exit_gate="acyclic; every FR covered; test task present"),
    StageSpec("design", "architecture", ("requirements", "planning"),
              description="Impact analysis + architecture decisions + risk classification",
              entry_gate="plan exists", exit_gate="decisions have rationale; impact analysed; risk classified"),
    StageSpec("design_approval", "architecture", ("design",), kind="checkpoint", high_impact=True,
              description="Human sign-off for high-impact designs",
              entry_gate="design.requires_approval", exit_gate="approved by a named human"),
    StageSpec("implementation", "implementation", ("requirements", "planning", "design"),
              description="Generate change set, apply in sandbox under policy",
              entry_gate="design approved or low-risk",
              exit_gate="no blocking policy finding; every code/test task traced to a change"),
    StageSpec("verification", "testing", ("implementation",), parallel_group="validate",
              description="Run the real build + test suite", exit_gate="build green, tests > 0, no failures"),
    StageSpec("security_review", "testing", ("implementation", "design"), parallel_group="validate",
              description="Policy scan of the full diff", exit_gate="no blocking finding"),
    StageSpec("documentation", "documentation", ("requirements", "design", "implementation"),
              parallel_group="validate", description="Feature doc + changelog",
              exit_gate="doc covers every FR; changelog present"),
    StageSpec("sync", "release", ("verification", "security_review", "documentation"), kind="join",
              description="Barrier: all validation branches must pass",
              entry_gate="all parallel branches finished"),
    StageSpec("release_approval", "release", ("sync",), kind="checkpoint", high_impact=True,
              description="Human final quality control",
              exit_gate="approved; require_approval findings acknowledged"),
    StageSpec("release", "release", ("requirements", "planning", "design", "implementation", "verification",
                                     "security_review", "documentation", "release_approval"),
              description="Package patch, release notes, engineering summary",
              entry_gate="release approved", exit_gate="patch + summary written"),
]}

# Control-flow edges (for validation and diagrams). Conditional / looping edges are marked.
CONTROL_EDGES: list[tuple[str, str, str]] = [
    ("requirements", "clarification", "needs clarification"),
    ("clarification", "requirements", "answers -> re-plan"),
    ("requirements", "planning", "clear"),
    ("planning", "design", ""),
    ("design", "design_approval", "high impact"),
    ("design", "implementation", "low/medium impact"),
    ("design_approval", "design", "rejected -> revise"),
    ("design_approval", "implementation", "approved"),
    ("implementation", "verification", "fan-out"),
    ("implementation", "security_review", "fan-out"),
    ("implementation", "documentation", "fan-out"),
    ("verification", "sync", "fan-in"),
    ("security_review", "sync", "fan-in"),
    ("documentation", "sync", "fan-in"),
    ("sync", "implementation", "failed -> rollback + rework"),
    ("sync", "release_approval", "all green"),
    ("release_approval", "implementation", "changes requested -> rework"),
    ("release_approval", "release", "approved"),
]


def downstream(stage: str) -> list[str]:
    """Every stage that transitively consumes ``stage`` (the invalidation set when it changes)."""
    out: list[str] = []
    queue = deque([stage])
    seen = {stage}
    while queue:
        cur = queue.popleft()
        for s in STAGES.values():
            if cur in s.consumes and s.name not in seen:
                seen.add(s.name)
                out.append(s.name)
                queue.append(s.name)
    return out


def topological_order() -> list[str]:
    indeg = {n: 0 for n in STAGES}
    for s in STAGES.values():
        for _ in s.consumes:
            indeg[s.name] += 1
    queue = deque(n for n, d in indeg.items() if d == 0)
    order: list[str] = []
    while queue:
        n = queue.popleft()
        order.append(n)
        for s in STAGES.values():
            if n in s.consumes:
                indeg[s.name] -= 1
                if indeg[s.name] == 0:
                    queue.append(s.name)
    if len(order) != len(STAGES):
        raise ValueError("stage dependency graph has a cycle")
    return order


def validate_control_flow() -> None:
    """Every data dependency must be reachable along control edges (no stage can run before its inputs)."""
    forward = [(a, b) for a, b, label in CONTROL_EDGES if "->" not in label or "fan" in label]
    for spec in STAGES.values():
        for dep in spec.consumes:
            if not _reachable(dep, spec.name, forward):
                raise ValueError(f"{spec.name} consumes {dep} but cannot be reached from it")


def _reachable(src: str, dst: str, edges: list[tuple[str, str]]) -> bool:
    queue, seen = deque([src]), {src}
    while queue:
        cur = queue.popleft()
        if cur == dst:
            return True
        for a, b in edges:
            if a == cur and b not in seen:
                seen.add(b)
                queue.append(b)
    return False


def mermaid() -> str:
    lines = ["flowchart TD", "  START([start]) --> requirements"]
    shapes = {"agent": ("[", "]"), "checkpoint": ("{{", "}}"), "join": ("((", "))")}
    for s in STAGES.values():
        o, c = shapes[s.kind]
        lines.append(f"  {s.name}{o}\"{s.name}\"{c}")
    for a, b, label in CONTROL_EDGES:
        arrow = "-.->" if "->" in label and "fan" not in label else "-->"
        lines.append(f"  {a} {arrow}|{label}| {b}" if label else f"  {a} {arrow} {b}")
    lines.append("  release --> END([end])")
    return "\n".join(lines)
