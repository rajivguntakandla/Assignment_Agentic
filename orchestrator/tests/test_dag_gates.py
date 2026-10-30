import pytest

from agentic_sdlc import dag
from agentic_sdlc.gates import entry_gate, exit_gate, waves


def test_dag_is_acyclic_and_control_flow_consistent():
    order = dag.topological_order()
    assert order.index("requirements") < order.index("planning") < order.index("design") < order.index("release")
    dag.validate_control_flow()


def test_downstream_invalidation_set():
    assert set(dag.downstream("implementation")) == {"verification", "security_review", "documentation", "sync",
                                                     "release_approval", "release"}
    assert "planning" in dag.downstream("requirements")
    assert dag.downstream("release") == []


def test_mermaid_contains_every_stage():
    text = dag.mermaid()
    assert all(name in text for name in dag.STAGES)


def test_waves_group_independent_tasks():
    tasks = [{"id": "T1", "depends_on": []}, {"id": "T2", "depends_on": []},
             {"id": "T3", "depends_on": ["T1", "T2"]}, {"id": "T4", "depends_on": ["T3"]}]
    assert waves(tasks) == [["T1", "T2"], ["T3"], ["T4"]]


def test_waves_detect_cycles():
    assert waves([{"id": "A", "depends_on": ["B"]}, {"id": "B", "depends_on": ["A"]}]) == []


@pytest.fixture
def state():
    return {"artifacts": {"requirements": {"functional_requirements": [
        {"id": "FR-1", "text": "x", "acceptance_criteria": ["y"]}]}}, "stages": {}, "approvals": {}}


def test_entry_gate_requires_upstream_artifacts(state):
    assert not entry_gate("design", state).passed
    state["artifacts"]["planning"] = {}
    assert entry_gate("design", state).passed


def test_entry_gate_blocks_unapproved_high_impact_design(state):
    state["artifacts"].update({"planning": {}, "design": {"requires_approval": True}})
    assert not entry_gate("implementation", state).passed
    state["approvals"]["design_approval"] = {"decision": "approve"}
    assert entry_gate("implementation", state).passed


def test_entry_gate_rejects_stale_inputs(state):
    state["artifacts"]["planning"] = {}
    state["stages"]["planning"] = {"status": "stale"}
    result = entry_gate("design", state)
    assert not result.passed and "stale" in result.reasons[0]


def test_release_requires_human_approval(state):
    assert any("approved" in r for r in entry_gate("release", state).reasons)


def test_planning_exit_gate_requires_coverage_and_tests(state):
    plan = {"tasks": [{"id": "T1", "kind": "code", "depends_on": [], "requirement_ids": []}]}
    reasons = exit_gate("planning", plan, state).reasons
    assert any("FR-1" in r for r in reasons) and any("test" in r for r in reasons)


def test_verification_exit_gate():
    assert exit_gate("verification", {"exit_code": 0, "tests": 10, "failures": 0, "errors": 0}, {}).passed
    assert not exit_gate("verification", {"exit_code": 0, "tests": 0, "failures": 0, "errors": 0}, {}).passed
    assert not exit_gate("verification", {"exit_code": 1, "tests": 10, "failures": 1, "errors": 0}, {}).passed
