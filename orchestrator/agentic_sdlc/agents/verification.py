from __future__ import annotations

from ..executor import verify
from ..settings import display_path
from .base import Agent, AgentContext, AgentResult


class VerificationAgent(Agent):
    """Executes the real build and test suite of the generated code (no LLM involved)."""

    name = "verification"

    def run(self, ctx: AgentContext) -> AgentResult:
        rt = ctx.rt
        round_ = ctx.inputs["implementation"].get("round", 1)
        log_file = rt.run_dir / "logs" / f"verification-r{round_}.log"
        report = verify(rt.runner, rt.settings.verify_command, rt.workspace.root, log_file)
        rt.audit.record("verification_result", stage=ctx.stage, parent_span_id=ctx.span_id, data={
            "passed": report.passed, "tests": report.tests, "failures": report.failures, "errors": report.errors,
            "duration_s": report.duration_s, "failed_tests": report.failed_tests[:10],
            "log": display_path(log_file)})
        artifact = report.model_dump(mode="json") | {"log_file": str(log_file), "round": round_}
        verdict = "green" if report.passed else "red"
        return AgentResult(artifact, [(f"Build r{round_} {verdict}: {report.tests} tests, "
                                       f"{report.failures} failures, {report.errors} errors",
                                       " ".join(report.command))])
