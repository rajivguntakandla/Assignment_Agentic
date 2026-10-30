"""Final engineering summary: plan/rationale, artifacts, risks/trade-offs/validation, assumptions, limitations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .settings import display_path

if TYPE_CHECKING:
    from .runtime import Runtime

LIMITATIONS = [
    "Verification runs the project's own test suite in a sandbox copy; it proves behaviour the tests cover, not "
    "behaviour they omit. Human review of the patch remains mandatory (release checkpoint).",
    "Policy rules are pattern-based (added lines only); they catch known-bad constructs, not novel logic flaws.",
    "Impact analysis is static and type-level (imports/identifiers); reflection and configuration-driven wiring "
    "are not modelled.",
    "In replay mode the LLM responses are recorded fixtures: orchestration, governance, policy, execution and "
    "verification are real, but generation quality is that of the recording, not a live model.",
]


def _rel(path: str | None, base: Any) -> str:
    if not path:
        return "-"
    try:
        return str(Path(path).relative_to(base))
    except ValueError:
        return str(path)


def _section(title: str, body: list[str]) -> list[str]:
    return [f"## {title}", "", *body, ""]


def render_summary(state: dict[str, Any], rt: Runtime, metrics: dict[str, Any],
                   traceability: list[dict[str, Any]] | None = None, final: bool = False) -> str:
    a = state.get("artifacts", {})
    spec, plan, design = a.get("requirements", {}), a.get("planning", {}), a.get("design", {})
    impl, ver, sec = a.get("implementation", {}), a.get("verification", {}), a.get("security_review", {})
    halt = state.get("halt")
    outcome = ("HALTED - " + halt.get("reason", "")) if halt else ("READY FOR MERGE (human-approved)" if final
                                                                   else "IN PROGRESS")
    out = [f"# Engineering Summary - {rt.scenario.title}", "",
           f"- **Run:** `{rt.run_id}`  ",
           f"- **Scenario:** {rt.scenario.kind} (`{rt.scenario.id}`)  ",
           f"- **Outcome:** {outcome}  ",
           f"- **LLM providers (in fallback order):** {', '.join(rt.llm.provider_names) or 'none'}  ",
           f"- **Execution path:** {' -> '.join(state.get('path', []))}", ""]

    out += _section("1. Requirement understanding", [
        f"**Intent:** {spec.get('intent', '-')}", "",
        *[f"- **{fr['id']}** {fr['text']}" + "".join(f"\n  - AC: {ac}" for ac in fr["acceptance_criteria"])
          for fr in spec.get("functional_requirements", [])],
        "", "**Non-functional:** " + ("; ".join(spec.get("non_functional_requirements", [])) or "-"),
        "", "**Clarifications (human):** " + ("; ".join(f"{k}: {v}" for k, v in
                                                       (state.get("clarifications") or {}).items()) or "none needed"),
        "", "**Out of scope:** " + ("; ".join(spec.get("out_of_scope", [])) or "-")])

    out += _section("2. Plan and rationale", [
        plan.get("rationale", ""), "",
        "| Task | Kind | Depends on | Requirements | Files |", "|---|---|---|---|---|",
        *[f"| {t['id']} {t['title']} | {t['kind']} | {', '.join(t.get('depends_on', [])) or '-'} | "
          f"{', '.join(t.get('requirement_ids', [])) or '-'} | {', '.join(f'`{f}`' for f in t.get('files', []))} |"
          for t in plan.get("tasks", [])],
        "", "**Execution waves:** " + " | ".join(", ".join(w) for w in plan.get("waves", []))])

    impact = design.get("impact", {})
    out += _section("3. Architecture and codebase reasoning", [
        design.get("summary", ""), "",
        f"- **Risk:** {design.get('risk_level', '-')} ({'; '.join(impact.get('risk_flags', [])) or 'none'}) - "
        f"human approval {'required' if design.get('requires_approval') else 'not required'}",
        f"- **Impacted modules:** {', '.join(impact.get('impacted_modules', [])) or '-'}",
        f"- **Affected endpoints:** {', '.join(impact.get('affected_endpoints', [])) or '-'}",
        f"- **Tables:** {', '.join(impact.get('tables', [])) or '-'}",
        *[f"- **Data flow:** {f}" for f in impact.get("data_flows", [])],
        "", *[f"- **{d['id']}** {d['decision']} - *{d['rationale']}*"
              + (f" (rejected: {', '.join(d.get('alternatives', []))})" if d.get("alternatives") else "")
              for d in design.get("decisions", [])]])

    out += _section("4. Artifacts", [
        f"- Patch: `{_rel(impl.get('patch_path'), rt.run_dir)}` (+{impl.get('lines_added', 0)} / "
        f"-{impl.get('lines_removed', 0)}); final release patch: `release/change.patch`",
        f"- Files: {', '.join(f'`{f}`' for f in impl.get('files_changed', [])) or '-'}",
        f"- Test methods (static count): {impl.get('tests_before', '-')} before -> "
        f"{impl.get('tests_after', '-')} after",
        f"- Run directory: `{display_path(rt.run_dir)}`",
        "- Audit log: `audit.jsonl` (hash-chained; `agentic audit verify <run>`); metrics: `metrics.json`",
        "- Stage artifacts: `artifacts/`; build logs: `logs/`; snapshots: `snapshots/`"])

    if traceability:
        out += _section("5. Traceability (requirement -> task -> code -> test)", [
            "| Requirement | Tasks | Files | Verified |", "|---|---|---|---|",
            *[f"| {r['requirement']} | {', '.join(r['tasks'])} | {', '.join(f'`{f}`' for f in r['files'])} | "
              f"{'yes' if r['verified'] else 'no'} |" for r in traceability]])

    findings = sec.get("findings", [])
    out += _section("6. Validation", [
        f"- **Build/test:** {'PASSED' if ver.get('passed') else 'FAILED' if ver else 'not run'} - "
        f"{ver.get('tests', 0)} tests, {ver.get('failures', 0)} failures, {ver.get('errors', 0)} errors "
        f"(`{' '.join(ver.get('command', []))}`, {ver.get('duration_s', '-')}s)",
        f"- **Rework cycles:** {state.get('rework_count', 0)}",
        f"- **Security/compliance findings:** {sec.get('counts', {})}",
        *[f"  - [{f['severity']}] {f['rule']} `{f.get('path') or ''}`:{f.get('line') or ''} {f['message']}"
          for f in findings],
        "- **Gates:** " + ", ".join(f"{k}={v.get('status')}" for k, v in sorted(state.get("stages", {}).items()))])

    sources = {g: r.get("source", "") for g, r in state.get("approvals", {}).items()}
    approvals = [f"- **{d['stage']}** by `{d['actor'].removeprefix('human:')}` ({sources.get(d['stage'], '')}): "
                 f"{d['summary'].split(': ', 1)[-1]} - {d['rationale']}"
                 for d in state.get("lineage", []) if d["actor"].startswith("human:")]
    out += _section("7. Human oversight (every decision, in order)", approvals or ["- none"])

    risks = list(design.get("risks", []))
    out += _section("8. Risks and trade-offs", [f"- {r}" for r in risks] or ["- none recorded"])

    out += _section("9. Decision lineage", [
        "| # | Stage | Actor | Decision | Rationale |", "|---|---|---|---|---|",
        *[f"| {d['id']} | {d['stage']} | {d['actor']} | {d['summary']} | {d['rationale'][:160]} |"
          for d in state.get("lineage", [])]])

    out += _section("10. Reliability metrics", [f"- **{k}:** {v}" for k, v in metrics.items()
                                                if not isinstance(v, dict)]
                    + [f"- **{k}:** {v}" for k, v in metrics.items() if isinstance(v, dict) and v])

    out += _section("11. Assumptions", [f"- {x}" for x in spec.get("assumptions", [])] or ["- none"])
    out += _section("12. Limitations", [f"- {x}" for x in LIMITATIONS])
    return "\n".join(out)
