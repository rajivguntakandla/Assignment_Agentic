"""End-to-end orchestration tests: real LangGraph graph, real policy/workspace/audit, fake build (fast)."""

from __future__ import annotations

import pytest

from agentic_sdlc.approvals import ManualApprovals, OneShotApprovals, ScenarioApprovals
from agentic_sdlc.audit import AuditLog
from agentic_sdlc.runner import RunCrashed, get_state, resume, start
from agentic_sdlc.safety import SafeStopController
from agentic_sdlc.scenario import load_scenario
from agentic_sdlc.settings import Budget
from agentic_sdlc.workspace import Workspace


def run(settings, name, run_id=None, approvals=None):
    scenario = load_scenario(name)
    outcome = start(scenario, settings, approvals or ScenarioApprovals(scenario), run_id or name)
    state, pending = get_state(outcome.run_id, settings)
    return outcome, state, pending


def events(settings, run_id, kind=None):
    evs = AuditLog(settings.run_dir(run_id) / "audit.jsonl", run_id).events()
    return [e for e in evs if kind is None or e["event"] == kind]


def test_greenfield_runs_parallel_validation_and_skips_design_approval(settings, fake_build):
    outcome, state, _ = run(settings, "greenfield")
    assert outcome.status == "completed"
    path = state["path"]
    assert "design_approval" not in path  # medium risk: no human design gate
    assert set(path[path.index("implementation") + 1: path.index("sync")]) == {
        "verification", "security_review", "documentation"}  # fan-out, then barrier
    assert state["artifacts"]["design"]["risk_level"] == "medium"
    assert state["artifacts"]["planning"]["waves"][0] == ["T1", "T2"]
    assert state["approvals"]["release_approval"]["approver"] == "tech-lead@shortener-team"
    release = settings.run_dir("greenfield") / "release"
    assert (release / "change.patch").read_text().count("+++ b/") == 6  # 4 code files + doc + changelog
    assert "READY FOR MERGE" in (release / "ENGINEERING_SUMMARY.md").read_text()
    assert AuditLog.verify(settings.run_dir("greenfield") / "audit.jsonl").ok
    assert outcome.metrics["stage_success_rate"] == 1.0


def test_brownfield_failure_triggers_rollback_rework_and_recovers(settings, fake_build):
    fake_build.results.extend([False, True])
    outcome, state, _ = run(settings, "brownfield")
    assert outcome.status == "completed"
    assert state["rework_count"] == 1
    assert state["path"].count("implementation") == 2
    assert "design_approval" in state["path"]  # schema change => human approval
    assert "schema/data-model change" in state["artifacts"]["design"]["impact"]["risk_flags"]
    assert any(e["data"].get("scope") == "rework" for e in events(settings, "brownfield", "retry"))
    assert events(settings, "brownfield", "rollback")
    replans = events(settings, "brownfield", "replan")
    assert any("implementation" in e["data"]["invalidated"] for e in replans)
    assert outcome.metrics["mttr_s"] is not None
    # round 2 feedback contained the failing test
    assert "FooTest.bar" in " ".join(state["rework_feedback"])


def test_ambiguous_clarification_and_design_rejection_loops(settings, fake_build):
    outcome, state, _ = run(settings, "ambiguous")
    assert outcome.status == "completed"
    path = state["path"]
    assert path[:3] == ["requirements", "clarification", "requirements"]
    assert path.count("design") == 2 and path.count("design_approval") == 2
    assert state["artifacts"]["requirements"]["revision"] == 2
    assert not state["artifacts"]["requirements"]["needs_clarification"]
    assert "safer" in state["clarifications"]
    assert state["design_feedback"] and "label boundaries" in state["design_feedback"][0]
    assert state["approvals"]["release_approval"]["acknowledged_findings"]
    lineage = [d for d in state["lineage"] if d["stage"] == "design"]
    assert any(d["supersedes"] for d in lineage)  # revision 2 supersedes revision 1 decisions


def test_policy_violation_halts_and_restores_baseline(settings, fake_build):
    outcome, state, _ = run(settings, "policy-violation")
    assert outcome.status == "halted"
    assert outcome.halt["stage"] == "implementation"
    assert fake_build.calls == 0  # never reached verification
    ws = Workspace(settings.run_dir("policy-violation"), settings.target_project)
    assert ws.tree_hash(ws.root) == ws.tree_hash(ws.baseline)
    blocked = [e for e in events(settings, "policy-violation", "policy_evaluated") if e["data"]["block"]]
    assert len(blocked) == 2
    assert "HALTED" in (settings.run_dir("policy-violation") / "release" / "ENGINEERING_SUMMARY.md").read_text()


def test_manual_approvals_pause_durably_and_resume(settings, fake_build):
    outcome, state, pending = run(settings, "brownfield", "manual", ManualApprovals())
    assert outcome.status == "paused" and pending[0]["gate"] == "design_approval"
    assert "implementation" not in state.get("stages", {})

    outcome = resume("manual", settings, OneShotApprovals({"decision": "approve", "approver": "alice"}))
    assert outcome.status == "paused" and outcome.pending[0]["gate"] == "release_approval"

    outcome = resume("manual", settings, OneShotApprovals({"decision": "approve", "approver": "bob"}))
    assert outcome.status == "completed"


def test_release_request_changes_loops_back_to_implementation(settings, fake_build):
    outcome, *_ = run(settings, "greenfield", "changes", ManualApprovals())
    assert outcome.pending[0]["gate"] == "release_approval"
    outcome = resume("changes", settings, OneShotApprovals(
        {"decision": "request_changes", "approver": "carol", "comment": "rename the DTO"}))
    assert outcome.status == "paused"
    state, _ = get_state("changes", settings)
    assert state["rework_count"] == 1 and state["path"].count("implementation") == 2
    assert state["rework_feedback"] == ["Reviewer: rename the DTO"]


def test_separation_of_duties_rejects_non_human_approver(settings, fake_build):
    run(settings, "greenfield", "sod", ManualApprovals())
    outcome = resume("sod", settings, OneShotApprovals({"decision": "approve", "approver": "agent:implementation"}))
    assert outcome.status == "halted" and "not a human identity" in outcome.halt["reason"]


def test_safe_stop_pauses_and_operator_can_continue_or_abort(settings, fake_build):
    scenario = load_scenario("greenfield")
    settings.faults = {"safe_stop:design": 1}  # same mechanism as `agentic stop <run>` (kill-switch file)
    outcome = start(scenario, settings, ManualApprovals(), "stop2")
    assert outcome.status == "paused" and outcome.pending[0]["type"] == "safe_stop"
    outcome = resume("stop2", settings, OneShotApprovals({"decision": "approve", "approver": "ops",
                                                          "action": "continue"}))
    assert outcome.status == "paused" and outcome.pending[0]["gate"] == "release_approval"

    settings.faults = {"safe_stop:planning": 1}
    outcome = start(scenario, settings, ManualApprovals(), "stop3")
    outcome = resume("stop3", settings, OneShotApprovals({"decision": "approve", "approver": "ops",
                                                          "action": "abort"}))
    assert outcome.status == "halted" and outcome.halt["code"] == "safe_stop_aborted"


def test_crash_is_recovered_from_checkpoint(settings, fake_build):
    settings.faults = {"crash:security_review": 1}
    scenario = load_scenario("greenfield")
    with pytest.raises(RunCrashed):
        start(scenario, settings, ScenarioApprovals(scenario), "crash")
    state, _ = get_state("crash", settings)
    assert state["stages"]["implementation"]["status"] == "completed"  # progress before the crash is durable
    outcome = resume("crash", settings, ScenarioApprovals(scenario))
    assert outcome.status == "completed"
    final, _ = get_state("crash", settings)
    assert final["path"].count("implementation") == 1  # completed stages are not re-executed
    assert events(settings, "crash", "run_crashed")


def test_llm_faults_are_retried_and_fall_back(settings, fake_build):
    settings.faults = {"llm_transient:planning": 2, "invalid_output:design": 1, "llm_down:documentation": 1}
    outcome, state, _ = run(settings, "greenfield", "faults")
    assert outcome.status == "completed"
    assert state["stages"]["documentation"]["provider"] == "deterministic-fallback"
    scopes = [e["data"]["scope"] for e in events(settings, "faults", "retry")]
    assert scopes.count("llm") == 3
    assert len(events(settings, "faults", "fallback")) == 1


def test_budget_exhaustion_is_a_safe_halt(settings, fake_build):
    settings.budget = Budget(max_llm_calls=2)
    outcome, *_ = run(settings, "greenfield", "budget")
    assert outcome.status == "halted" and outcome.halt["code"] == "budget_exceeded"
    assert events(settings, "budget", "budget_exceeded")


def test_rework_budget_is_bounded(settings, fake_build):
    settings.budget = Budget(max_rework_cycles=1)
    fake_build.results.extend([False, False])
    outcome, state, _ = run(settings, "brownfield", "bounded")
    assert outcome.status == "halted" and "rework budget exhausted" in outcome.halt["reason"]
    assert state["rework_count"] == 1


def test_operator_stop_request_pauses_running_graph(settings, fake_build):
    scenario = load_scenario("greenfield")
    outcome = start(scenario, settings, ManualApprovals(), "opstop")
    assert outcome.pending[0]["gate"] == "release_approval"
    SafeStopController(settings.run_dir("opstop")).request("change freeze")
    outcome = resume("opstop", settings, OneShotApprovals({"decision": "approve", "approver": "dana"}))
    assert outcome.status == "paused" and outcome.pending[0]["type"] == "safe_stop"
    assert outcome.pending[0]["reason"] == "change freeze"


def test_run_ids_cannot_be_reused(settings, fake_build):
    run(settings, "greenfield", "dup")
    with pytest.raises(FileExistsError):
        run(settings, "greenfield", "dup")
