from __future__ import annotations

import re

from ..models import PolicyFinding
from ..policy import count_tests, summarize
from .base import Agent, AgentContext, AgentResult

_NEW_ENDPOINT = re.compile(r"@(Get|Post|Put|Delete|Patch)Mapping")


class SecurityReviewAgent(Agent):
    """Independent re-scan of the complete diff (defence in depth over the implementation-time check)."""

    name = "security_review"

    def run(self, ctx: AgentContext) -> AgentResult:
        rt = ctx.rt
        design = ctx.inputs["design"]
        _, diffs = rt.workspace.diff()
        findings = rt.policy.check_diff(diffs, design["planned_files"], count_tests(rt.workspace.baseline),
                                        count_tests(rt.workspace.root))
        for d in diffs:
            for line_no, text in d.added_lines:
                if d.path.startswith("src/main/") and _NEW_ENDPOINT.search(text):
                    findings.append(PolicyFinding(
                        rule="SEC-200-new-endpoint", category="security", severity="warn", path=d.path,
                        line=line_no, message="New HTTP endpoint: confirm input validation and rate limiting"))
        counts = summarize(findings)
        rt.audit.record("policy_evaluated", stage=ctx.stage, parent_span_id=ctx.span_id,
                        data={**counts, "scope": "full-diff"})
        artifact = {"findings": [f.model_dump() for f in findings], "counts": counts,
                    "blocking": counts["block"] > 0, "files_reviewed": [d.path for d in diffs]}
        return AgentResult(artifact, [(f"Security review: {counts['block']} blocking, "
                                       f"{counts['require_approval']} need approval, {counts['warn']} warnings",
                                       "policy-as-code scan of all added lines")])
