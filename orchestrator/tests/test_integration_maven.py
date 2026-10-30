"""Slow end-to-end: real Maven build of the generated code. Run with `pytest -m integration`."""

import shutil

import pytest

from agentic_sdlc.approvals import ScenarioApprovals
from agentic_sdlc.runner import get_state, start
from agentic_sdlc.scenario import load_scenario

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(shutil.which("mvn") is None, reason="Maven not installed")]


@pytest.mark.parametrize("name,expected_rework", [("greenfield", 0), ("brownfield", 1), ("ambiguous", 0)])
def test_scenario_builds_and_passes_real_tests(settings, name, expected_rework):
    scenario = load_scenario(name)
    outcome = start(scenario, settings, ScenarioApprovals(scenario), f"it-{name}")
    state, _ = get_state(outcome.run_id, settings)
    assert outcome.status == "completed", outcome.halt
    assert state["rework_count"] == expected_rework
    ver = state["artifacts"]["verification"]
    assert ver["passed"] and ver["tests"] > 45 and ver["failures"] == 0
