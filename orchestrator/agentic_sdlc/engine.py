"""The LangGraph orchestration engine.

Every agent stage runs inside the same governance envelope:

    safe-stop check -> budget check -> entry gate -> (bounded retries -> agent -> fallbacks) -> exit gate
    -> output fingerprint -> re-plan (invalidate downstream if the output changed) -> lineage + audit

Human checkpoints (clarification, design approval, release approval, safe stop) are LangGraph ``interrupt``s:
the run pauses durably in the checkpoint store and resumes exactly where it stopped, in this process or another.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .agents import AGENTS, AgentContext, AgentFailure
from .approvals import validate_decision
from .audit import fingerprint, now_iso
from .dag import STAGES, downstream, validate_control_flow
from .gates import GateResult, entry_gate, exit_gate
from .report import render_summary
from .resilience import BudgetExceeded, OutputValidationError, PermanentError, TransientError, retry_call
from .runtime import Runtime
from .state import RunState

PARALLEL = ("verification", "security_review", "documentation")


class Engine:
    def __init__(self, rt: Runtime, checkpointer: Any):
        validate_control_flow()
        self.rt = rt
        self.graph = self._build().compile(checkpointer=checkpointer)

    # ================================================================================================ graph
    def _build(self) -> StateGraph:
        g = StateGraph(RunState)
        for name in ("requirements", "planning", "design", "implementation", *PARALLEL, "release"):
            g.add_node(name, self._agent_node(name))
        g.add_node("clarification", self._clarification)
        g.add_node("design_approval", self._design_approval)
        g.add_node("sync", self._sync)
        g.add_node("release_approval", self._release_approval)
        g.add_node("halted", self._halted)

        g.add_edge(START, "requirements")
        g.add_conditional_edges("requirements", self._after_requirements, ["clarification", "planning", "halted"])
        g.add_conditional_edges("clarification", self._ok_or_halt("requirements"), ["requirements", "halted"])
        g.add_conditional_edges("planning", self._ok_or_halt("design"), ["design", "halted"])
        g.add_conditional_edges("design", self._after_design, ["design_approval", "implementation", "halted"])
        g.add_conditional_edges("design_approval", self._after_design_approval,
                                ["implementation", "design", "halted"])
        g.add_conditional_edges("implementation", self._after_implementation, [*PARALLEL, "halted"])
        g.add_edge(list(PARALLEL), "sync")  # synchronisation barrier: waits for all three branches
        g.add_conditional_edges("sync", self._after_sync, ["release_approval", "implementation", "halted"])
        g.add_conditional_edges("release_approval", self._after_release_approval,
                                ["release", "implementation", "halted"])
        g.add_conditional_edges("release", self._ok_or_halt(END), [END, "halted"])
        g.add_edge("halted", END)
        return g

    # ============================================================================================ routing
    @staticmethod
    def _ok_or_halt(target: str):
        def route(state: RunState) -> str:
            return "halted" if state.get("halt") else target
        return route

    @staticmethod
    def _after_requirements(state: RunState) -> str:
        if state.get("halt"):
            return "halted"
        return "clarification" if state["artifacts"]["requirements"].get("needs_clarification") else "planning"

    @staticmethod
    def _after_design(state: RunState) -> str:
        if state.get("halt"):
            return "halted"
        return "design_approval" if state["artifacts"]["design"].get("requires_approval") else "implementation"

    @staticmethod
    def _after_design_approval(state: RunState) -> str:
        if state.get("halt"):
            return "halted"
        return "implementation" if state["approvals"]["design_approval"]["decision"] == "approve" else "design"

    @staticmethod
    def _after_implementation(state: RunState) -> list[str] | str:
        return "halted" if state.get("halt") else list(PARALLEL)  # parallel fan-out

    @staticmethod
    def _after_sync(state: RunState) -> str:
        if state.get("halt"):
            return "halted"
        return "release_approval" if state["stages"]["sync"]["status"] == "completed" else "implementation"

    @staticmethod
    def _after_release_approval(state: RunState) -> str:
        if state.get("halt"):
            return "halted"
        return "release" if state["approvals"]["release_approval"]["decision"] == "approve" else "implementation"

    # ======================================================================================= agent nodes
    def _agent_node(self, name: str):
        agent = AGENTS[name]

        def node(state: RunState) -> dict[str, Any]:
            stopped = self._safe_stop_checkpoint(name, state)
            if stopped:
                return stopped
            span = uuid.uuid4().hex[:16]
            try:
                self.rt.budget.check_wall_clock()
            except BudgetExceeded as exc:
                return self._fail(name, "budget_exceeded", str(exc), span)

            entry = entry_gate(name, state)
            self._audit_gate(name, "entry", entry, span)
            if not entry.passed:
                return self._fail(name, "entry_gate_failed", "; ".join(entry.reasons), span)

            if self.rt.faults.take(f"crash:{name}"):
                # Simulates a process crash mid-run; recovery is `agentic resume` from the last checkpoint.
                raise RuntimeError(f"injected crash in stage {name}")

            variant, feedback = self._variant(name, state), self._feedback(name, state)
            inputs = {d: state["artifacts"][d] for d in STAGES[name].consumes if d in state.get("artifacts", {})}
            ctx = AgentContext(self.rt, name, inputs, dict(state), span, variant, feedback)
            started = time.monotonic()
            self.rt.audit.record("stage_started", stage=name, span_id=span, data={
                "variant": variant, "inputs": {k: fingerprint(v) for k, v in inputs.items()},
                "feedback_items": len(feedback)})

            def on_retry(attempt: int, exc: BaseException, delay: float) -> None:
                self.rt.audit.record("retry", stage=name, parent_span_id=span, data={
                    "scope": "stage", "attempt": attempt, "reason": str(exc)[:300], "backoff_s": round(delay, 3)})

            try:
                result = retry_call(lambda _a: agent.run(ctx), max_retries=self.rt.settings.stage_max_attempts - 1,
                                    backoff_base_s=self.rt.settings.llm_backoff_base_s, retry_on=(TransientError,),
                                    on_retry=on_retry)
            except BudgetExceeded as exc:
                return self._fail(name, "budget_exceeded", str(exc), span, started)
            except (AgentFailure, PermanentError, TransientError, OutputValidationError) as exc:
                return self._fail(name, "agent_failed", str(exc), span, started)

            exit_ = exit_gate(name, result.artifact, state)
            self._audit_gate(name, "exit", exit_, span)
            duration = round(time.monotonic() - started, 3)
            record = {"status": "completed" if exit_.passed else "failed", "provider": result.provider,
                      "duration_s": duration, "variant": variant, "output_fp": fingerprint(result.artifact),
                      "input_fp": {k: fingerprint(v) for k, v in inputs.items()},
                      "exit_gate": exit_.reasons, "finished_at": now_iso()}
            self.rt.audit.record("stage_completed" if exit_.passed else "stage_failed", stage=name, span_id=span,
                                 data={"duration_s": duration, "provider": result.provider,
                                       "output_fp": record["output_fp"], "reasons": exit_.reasons})
            self.rt.save_artifact(f"{name}.json", result.artifact)

            update: dict[str, Any] = {
                "artifacts": {name: result.artifact},
                "stages": {name: record, **self._replan(name, state, record["output_fp"])},
                "lineage": self._decisions(name, state, result.decisions, f"agent:{name}", record["input_fp"]),
                "path": [name],
            }
            if not exit_.passed and name not in PARALLEL:
                update["halt"] = self._halt_info(name, "exit_gate_failed", "; ".join(exit_.reasons))
            elif name == "release":
                update["status"] = "completed"
            return update

        return node

    def _variant(self, name: str, state: RunState) -> int:
        if name == "requirements":
            return state.get("clarification_rounds", 0) + 1
        if name == "design":
            return state.get("design_revisions", 0) + 1
        if name == "planning":
            return (state.get("artifacts", {}).get("planning") or {}).get("revision", 0) + 1
        if name in ("implementation", "documentation"):
            return state.get("rework_count", 0) + 1
        return 1

    @staticmethod
    def _feedback(name: str, state: RunState) -> list[str]:
        if name == "design":
            return list(state.get("design_feedback", []))
        if name == "implementation":
            return list(state.get("rework_feedback", []))
        return []

    def _replan(self, name: str, state: RunState, output_fp: str) -> dict[str, Any]:
        """Dynamic re-planning: a changed output invalidates every stage that (transitively) consumed it."""
        previous = state.get("stages", {}).get(name)
        if not previous or previous.get("output_fp") in (None, output_fp):
            return {}
        stages = state.get("stages", {})
        invalidated = [s for s in downstream(name) if stages.get(s, {}).get("status") in ("completed", "failed")
                       and name not in STAGES[s].feeds_back_to]
        if not invalidated:
            return {}
        self.rt.audit.record("replan", stage=name, data={
            "trigger": f"{name} output changed", "previous_fp": previous.get("output_fp"), "new_fp": output_fp,
            "invalidated": invalidated})
        return {s: {**stages[s], "status": "stale", "invalidated_by": name} for s in invalidated}

    def _decisions(self, stage: str, state: RunState, items: list[tuple[str, str]], actor: str,
                   inputs: dict[str, str]) -> list[dict[str, Any]]:
        prior = [d for d in state.get("lineage", []) if d["stage"] == stage]
        out = []
        for i, (summary, rationale) in enumerate(items):
            # Stage-scoped ids stay unique even when parallel branches append in the same step.
            out.append({"id": f"{stage}#{len(prior) + i + 1}", "stage": stage, "actor": actor, "summary": summary,
                        "rationale": rationale, "inputs": inputs, "at": now_iso(),
                        "supersedes": prior[i]["id"] if i < len(prior) else None})
        return out

    # =================================================================================== safe stop / fail
    def _safe_stop_checkpoint(self, stage: str, state: RunState) -> dict[str, Any] | None:
        """Honoured at agent-stage boundaries only. Human checkpoints are already pauses, and LangGraph matches resume
        values to interrupts by their order inside a node, so a node must never contain two interrupt sites."""
        if self.rt.faults.take(f"safe_stop:{stage}"):
            self.rt.safe_stop.request(f"injected safe-stop before {stage}")
        if not self.rt.safe_stop.requested():
            return None
        key = f"safe_stop:{stage}:{len(state.get('path', []))}"
        reason = self.rt.safe_stop.reason()
        self.rt.audit.record("safe_stop_requested", stage=stage, data={"reason": reason}, dedupe_key=key)
        self.rt.audit.record("approval_requested", stage=stage, data={"gate_key": key, "gate": "safe_stop"},
                             dedupe_key=key + ":req")
        answer = interrupt({"type": "safe_stop", "gate": "safe_stop", "gate_key": key, "stage": stage,
                            "reason": reason, "options": ["continue", "abort"]})
        self.rt.safe_stop.clear()
        answer = answer if isinstance(answer, dict) else {}
        action = answer.get("action", "abort")
        _, err = validate_decision({**answer, "decision": "approve"}, {"approve"})
        self.rt.audit.record("approval_decided", actor=f"{'rejected-identity' if err else 'human'}:"
                                                       f"{answer.get('approver')}", stage=stage,
                             data={"gate_key": key, "gate": "safe_stop", "action": action, "error": err,
                                   "source": answer.get("source")}, dedupe_key=key + ":dec")
        if action == "continue" and not err:
            self.rt.audit.record("safe_stop_resumed", stage=stage, dedupe_key=key + ":resumed")
            return None
        return {"halt": self._halt_info(stage, "safe_stop_aborted", err or f"operator aborted before {stage}"),
                "path": [stage]}

    def _fail(self, name: str, code: str, reason: str, span: str, started: float | None = None) -> dict[str, Any]:
        duration = round(time.monotonic() - started, 3) if started else 0.0
        self.rt.audit.record("stage_failed", stage=name, span_id=span, data={"code": code, "reason": reason[:500],
                                                                             "duration_s": duration})
        if code == "budget_exceeded":
            self.rt.audit.record("budget_exceeded", stage=name, data={"reason": reason})
        return {"stages": {name: {"status": "failed", "error": reason[:500], "code": code, "finished_at": now_iso()}},
                "halt": self._halt_info(name, code, reason), "path": [name]}

    @staticmethod
    def _halt_info(stage: str, code: str, reason: str) -> dict[str, Any]:
        return {"stage": stage, "code": code, "reason": reason[:500], "at": now_iso()}

    def _audit_gate(self, stage: str, kind: str, result: GateResult, span: str) -> None:
        self.rt.audit.record("gate_evaluated", stage=stage, parent_span_id=span, data={
            "gate": kind, "passed": result.passed, "reasons": result.reasons})

    # ======================================================================================= checkpoints
    def _human_checkpoint(self, state: RunState, gate: str, payload: dict[str, Any],
                          allowed: set[str]) -> tuple[dict[str, Any] | None, str | None, str]:
        key = f"{gate}:{len(state.get('path', []))}"
        self.rt.audit.record("approval_requested", stage=gate, data={"gate_key": key, "gate": gate,
                                                                     "summary": payload.get("summary")},
                             dedupe_key=key + ":req")
        answer = interrupt({"type": payload.pop("type", "approval"), "gate": gate, "gate_key": key, **payload,
                            "options": sorted(allowed)})
        decision, err = validate_decision(answer, allowed)
        who = (answer or {}).get("approver") if isinstance(answer, dict) else None
        self.rt.audit.record("approval_decided", actor=f"{'rejected-identity' if err else 'human'}:{who}", stage=gate,
                             data={
            "gate_key": key, "gate": gate, "decision": (answer or {}).get("decision"), "error": err,
            "comment": (answer or {}).get("comment", ""), "source": (answer or {}).get("source")},
            dedupe_key=key + ":dec")
        return decision, err, key

    def _approval_update(self, gate: str, state: RunState, decision: dict[str, Any]) -> dict[str, Any]:
        record = {"decision": decision["decision"], "approver": decision["approver"],
                  "comment": decision.get("comment", ""), "source": decision.get("source", ""),
                  "acknowledged_findings": bool(decision.get("acknowledged_findings")),
                  "promote": bool(decision.get("promote")), "at": now_iso()}
        lineage = self._decisions(gate, state, [(f"{gate}: {decision['decision']}",
                                                 decision.get("comment") or "human checkpoint")],
                                  f"human:{decision['approver']}", {})
        return {"approvals": {gate: record}, "lineage": lineage, "path": [gate],
                "stages": {gate: {"status": "completed", "decision": decision["decision"], "finished_at": now_iso()}}}

    def _clarification(self, state: RunState) -> dict[str, Any]:
        rounds = state.get("clarification_rounds", 0)
        if rounds >= self.rt.settings.budget.max_clarification_rounds:
            return {"halt": self._halt_info("clarification", "budget_exceeded", "clarification rounds exhausted"),
                    "path": ["clarification"]}
        spec = state["artifacts"]["requirements"]
        decision, err, _ = self._human_checkpoint(state, "clarification", {
            "type": "clarification", "summary": f"{len(spec['ambiguities'])} open question(s) on '{spec['title']}'",
            "questions": spec["ambiguities"]}, {"approve", "abort"})
        if err or decision["decision"] == "abort":
            return {"halt": self._halt_info("clarification", "rejected", err or "requirement withdrawn"),
                    "path": ["clarification"]}
        answers = dict(decision.get("answers") or {})
        if decision.get("accept_defaults"):
            answers = {a["term"]: a["proposed_default"] for a in spec["ambiguities"]} | answers
        update = self._approval_update("clarification", state, decision)
        update.update({"clarifications": {**(state.get("clarifications") or {}), **answers},
                       "clarification_rounds": rounds + 1})
        return update

    def _design_approval(self, state: RunState) -> dict[str, Any]:
        design = state["artifacts"]["design"]
        decision, err, _ = self._human_checkpoint(state, "design_approval", {
            "summary": design["summary"], "risk_level": design["risk_level"],
            "risk_flags": design["impact"]["risk_flags"], "planned_files": design["planned_files"],
            "schema_changes": design["schema_changes"], "api_changes": design["api_changes"],
            "decisions": design["decisions"]}, {"approve", "reject", "abort"})
        if err or decision["decision"] == "abort":
            return {"halt": self._halt_info("design_approval", "rejected", err or "design aborted by reviewer"),
                    "path": ["design_approval"]}
        update = self._approval_update("design_approval", state, decision)
        if decision["decision"] == "reject":
            revisions = state.get("design_revisions", 0) + 1
            if revisions > self.rt.settings.budget.max_design_revisions:
                update["halt"] = self._halt_info("design_approval", "budget_exceeded", "design revisions exhausted")
            update.update({"design_revisions": revisions,
                           "design_feedback": [*state.get("design_feedback", []), decision.get("comment", "")]})
        return update

    def _sync(self, state: RunState) -> dict[str, Any]:
        """Barrier after the parallel validation branches; decides release vs rollback + rework vs halt."""
        stages = state.get("stages", {})
        branch = {s: stages.get(s, {}).get("status") for s in PARALLEL}
        self.rt.audit.record("gate_evaluated", stage="sync", data={"gate": "barrier", "branches": branch,
                                                                   "passed": all(v == "completed" for v in
                                                                                 branch.values())})
        if state.get("halt"):
            return {"path": ["sync"]}
        if all(v == "completed" for v in branch.values()):
            return {"stages": {"sync": {"status": "completed", "finished_at": now_iso()}}, "path": ["sync"]}
        if branch["documentation"] != "completed" and branch["verification"] == branch["security_review"] == \
                "completed":
            return {"halt": self._halt_info("sync", "exit_gate_failed", "documentation gate failed"), "path": ["sync"]}

        art = state["artifacts"]
        feedback: list[str] = []
        if branch["verification"] != "completed":
            ver = art.get("verification", {})
            feedback.append(f"Build/tests failed ({ver.get('failures', 0)} failures, {ver.get('errors', 0)} errors)")
            feedback.extend(ver.get("failed_tests", [])[:10])
        if branch["security_review"] != "completed":
            feedback.extend(f"{f['rule']} {f.get('path')}:{f.get('line')} {f['message']}"
                            for f in art.get("security_review", {}).get("findings", []) if f["severity"] == "block")
        return self._rework(state, "sync", "validation failed", feedback)

    def _rework(self, state: RunState, stage: str, trigger: str, feedback: list[str]) -> dict[str, Any]:
        """Roll back the workspace to the pre-implementation snapshot and route back with feedback (bounded)."""
        count = state.get("rework_count", 0)
        if count >= self.rt.settings.budget.max_rework_cycles:
            return {"halt": self._halt_info(stage, "budget_exceeded", f"rework budget exhausted after {count} "
                                                                      f"cycle(s): {trigger}"), "path": [stage]}
        snapshot = state["artifacts"]["implementation"]["snapshot"]
        restored = self.rt.workspace.restore(snapshot)
        self.rt.audit.record("rollback", stage=stage, data={"reason": trigger, "snapshot": snapshot,
                                                            "restored_hash": restored})
        invalidated = ["implementation", *downstream("implementation")]
        self.rt.audit.record("replan", stage=stage, data={"trigger": trigger, "invalidated": invalidated,
                                                          "feedback": feedback[:10]})
        self.rt.audit.record("retry", stage=stage, data={"scope": "rework", "attempt": count + 1, "reason": trigger})
        stages = state.get("stages", {})
        stale = {s: {**stages[s], "status": "stale", "invalidated_by": stage} for s in invalidated if s in stages}
        return {"rework_count": count + 1, "rework_feedback": feedback, "path": [stage],
                "stages": {**stale, stage: {"status": "failed", "reason": trigger, "finished_at": now_iso()}}}

    def _release_approval(self, state: RunState) -> dict[str, Any]:
        art = state["artifacts"]
        needs_ack = [f for f in art["security_review"]["findings"] + art["implementation"]["policy_findings"]
                     if f["severity"] == "require_approval"]
        decision, err, _ = self._human_checkpoint(state, "release_approval", {
            "summary": art["implementation"]["summary"],
            "verification": {k: art["verification"][k] for k in ("passed", "tests", "failures", "errors")},
            "files_changed": art["implementation"]["files_changed"], "patch": art["implementation"]["patch_path"],
            "findings_requiring_acknowledgement": needs_ack, "rework_cycles": state.get("rework_count", 0)},
            {"approve", "request_changes", "reject"})
        if err:
            return {"halt": self._halt_info("release_approval", "rejected", err), "path": ["release_approval"]}
        if decision["decision"] == "approve" and needs_ack and not decision.get("acknowledged_findings"):
            return {"halt": self._halt_info("release_approval", "rejected", "approval did not acknowledge findings "
                                            "that require explicit sign-off"), "path": ["release_approval"]}
        update = self._approval_update("release_approval", state, decision)
        if decision["decision"] == "reject":
            update["halt"] = self._halt_info("release_approval", "rejected", decision.get("comment") or "rejected")
        elif decision["decision"] == "request_changes":
            rework = self._rework(state, "release_approval", "reviewer requested changes",
                                  [f"Reviewer: {decision.get('comment', '')}"])
            rework.pop("path")
            rework_stages = rework.pop("stages", {})
            rework_stages.pop("release_approval", None)
            update["stages"].update(rework_stages)
            update.update(rework)
        return update

    def _halted(self, state: RunState) -> dict[str, Any]:
        halt = state["halt"]
        ws = self.rt.workspace
        ws.snapshot("failed-state")  # kept for forensics
        restored = ws.restore("baseline")
        self.rt.audit.record("rollback", stage="halted", data={"reason": f"halt: {halt['code']}",
                                                               "snapshot": "baseline", "restored_hash": restored,
                                                               "forensic_copy": "snapshots/failed-state"})
        return {"status": "halted", "path": ["halted"]}

    def write_summary(self, state: dict[str, Any], metrics: dict[str, Any], final: bool) -> None:
        out = self.rt.run_dir / "release"
        out.mkdir(exist_ok=True)
        traceability = (state.get("artifacts", {}).get("release") or {}).get("traceability")
        (out / "ENGINEERING_SUMMARY.md").write_text(render_summary(state, self.rt, metrics, traceability, final))
