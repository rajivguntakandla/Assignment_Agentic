import pytest

from agentic_sdlc.cli import main
from agentic_sdlc.executor import CommandRunner, parse_surefire, scrubbed_env, verify
from agentic_sdlc.resilience import PermanentError

SUREFIRE = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="FooTest" tests="3" failures="1" errors="0" skipped="1">
  <testcase classname="FooTest" name="ok"/>
  <testcase classname="FooTest" name="limit">
    <failure message="Status expected:&lt;410&gt; but was:&lt;302&gt;"/>
  </testcase>
  <testcase classname="FooTest" name="skipped"><skipped/></testcase>
</testsuite>"""


def test_command_allowlist_and_missing_tools(tmp_path):
    runner = CommandRunner(timeout_s=5)
    with pytest.raises(PermanentError, match="allow-listed"):
        runner.run(["bash", "-c", "echo pwned"], tmp_path, tmp_path / "log")
    with pytest.raises(PermanentError, match="allow-listed"):
        runner.run([], tmp_path, tmp_path / "log")


def test_environment_is_scrubbed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("GITHUB_TOKEN", "y")
    monkeypatch.setenv("JAVA_HOME", "/jdk")
    env = scrubbed_env()
    assert "ANTHROPIC_API_KEY" not in env and "GITHUB_TOKEN" not in env and env["JAVA_HOME"] == "/jdk"


def test_surefire_parsing(tmp_path):
    reports = tmp_path / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    (reports / "TEST-FooTest.xml").write_text(SUREFIRE)
    tests, failures, errors, skipped, failed = parse_surefire(tmp_path)
    assert (tests, failures, errors, skipped) == (3, 1, 0, 1)
    assert failed == ["FooTest.limit: Status expected:<410> but was:<302>"]


def test_verify_requires_tests_to_have_run(tmp_path):
    report = verify(CommandRunner(timeout_s=5), ["true"], tmp_path, tmp_path / "logs" / "v.log")
    assert report.exit_code == 0 and not report.passed  # green exit but zero tests is not evidence
    report = verify(CommandRunner(timeout_s=5), ["false"], tmp_path, tmp_path / "logs" / "v.log")
    assert not report.passed and report.exit_code == 1


def test_cli_end_to_end(tmp_path, fake_build, capsys):
    home = ["--home", str(tmp_path)]
    assert main(home + ["run", "--scenario", "greenfield", "--llm", "offline", "--run-id", "c1"]) == 3  # paused
    assert "waiting for human: release_approval" in capsys.readouterr().out
    assert main(home + ["status", "c1"]) == 0
    assert "PENDING: release_approval" in capsys.readouterr().out
    assert main(home + ["resume", "c1", "--decision", "approve", "--approver", "erin"]) == 0
    assert "COMPLETED" in capsys.readouterr().out
    assert main(home + ["audit", "verify", "c1"]) == 0
    assert "VALID" in capsys.readouterr().out
    assert main(home + ["audit", "show", "c1"]) == 0
    assert "approval_decided" in capsys.readouterr().out
    assert main(home + ["metrics", "--prometheus"]) == 0
    assert "agentic_run_success_rate 1.0" in capsys.readouterr().out
    assert main(home + ["graph"]) == 0
    assert "flowchart TD" in capsys.readouterr().out
    assert main(home + ["run", "--scenario", "greenfield", "--run-id", "c1"]) == 2  # id reuse refused


def test_cli_stop_and_tampered_audit(tmp_path, fake_build, capsys):
    home = ["--home", str(tmp_path)]
    main(home + ["run", "--scenario", "greenfield", "--llm", "offline", "--run-id", "c2"])
    assert main(home + ["stop", "c2", "--reason", "freeze"]) == 0
    assert (tmp_path / "c2" / "STOP").read_text() == "freeze"
    audit = tmp_path / "c2" / "audit.jsonl"
    audit.write_text(audit.read_text().replace("release_approval", "release_approva1", 1))
    assert main(home + ["audit", "verify", "c2"]) == 1
    assert "INVALID" in capsys.readouterr().out


def test_adhoc_requirement_without_llm_uses_safe_fallbacks_then_halts(settings, fake_build):
    """No provider at all: analysis stages degrade to deterministic fallbacks; code generation fails closed."""
    from agentic_sdlc.approvals import ScenarioApprovals
    from agentic_sdlc.runner import get_state, start
    from agentic_sdlc.scenario import adhoc_scenario

    scenario = adhoc_scenario("Add a stats endpoint that returns the total clicks for a link within 200 ms.",
                              "brownfield")
    outcome = start(scenario, settings, ScenarioApprovals(scenario), "adhoc")
    state, _ = get_state("adhoc", settings)
    assert outcome.status == "halted" and outcome.halt["stage"] == "implementation"
    assert "no safe fallback" in outcome.halt["reason"]
    for stage in ("requirements", "planning", "design"):
        assert state["stages"][stage]["provider"] == "deterministic-fallback"
    assert state["artifacts"]["planning"]["tasks"][-1]["kind"] == "docs"


def test_interactive_approvals_capture_human_input(monkeypatch, capsys):
    from agentic_sdlc.approvals import InteractiveApprovals, validate_decision

    replies = iter(["", "HTTP 400 for listed domains", "approve", "looks good", "y", "continue"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(replies))
    source = InteractiveApprovals("frank@team")
    clar = source.decide({"type": "clarification", "gate": "clarification", "questions": [
        {"term": "safer", "question": "?", "proposed_default": "denylist"},
        {"term": "acceptance", "question": "?", "proposed_default": "x"}]})
    assert clar["answers"] == {"safer": "denylist", "acceptance": "HTTP 400 for listed domains"}
    rel = source.decide({"type": "approval", "gate": "release_approval", "options": ["approve"],
                         "findings_requiring_acknowledgement": [{"rule": "CC-005"}]})
    assert rel["decision"] == "approve" and rel["acknowledged_findings"] and rel["approver"] == "frank@team"
    assert source.decide({"type": "safe_stop", "gate": "safe_stop"})["action"] == "continue"
    assert "HUMAN CHECKPOINT" in capsys.readouterr().out
    assert validate_decision("nope", {"approve"})[1] == "decision must be an object"
    assert "not in" in validate_decision({"decision": "maybe", "approver": "x"}, {"approve"})[1]
