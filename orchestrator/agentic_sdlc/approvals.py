"""Sources of human decisions for checkpoints.

* ``ScenarioApprovals`` - decisions pre-recorded in the scenario file by a named reviewer (unattended demo/CI).
  They are labelled as such in the audit log; they still flow through the same interrupt/resume mechanism.
* ``InteractiveApprovals`` - prompts on the terminal.
* ``ManualApprovals`` - never decides; the run pauses durably and is resumed later with ``agentic resume``.
"""

from __future__ import annotations

import json
from typing import Any

from .scenario import Scenario

NON_HUMAN_IDENTITIES = {"", "agent", "orchestrator", "system", "ai", "llm", "claude", "bot"}


def validate_decision(value: Any, allowed: set[str]) -> tuple[dict[str, Any] | None, str | None]:
    """Separation of duties: a decision must name a human approver and use an allowed verb."""
    if not isinstance(value, dict):
        return None, "decision must be an object"
    approver = str(value.get("approver", "")).strip()
    if approver.lower() in NON_HUMAN_IDENTITIES or approver.lower().startswith(("agent:", "bot:")):
        return None, f"approver {approver!r} is not a human identity"
    decision = value.get("decision")
    if decision not in allowed:
        return None, f"decision {decision!r} not in {sorted(allowed)}"
    return value, None


class ApprovalSource:
    label = "unknown"

    def decide(self, request: dict[str, Any]) -> dict[str, Any] | None:  # pragma: no cover - interface
        raise NotImplementedError


class ManualApprovals(ApprovalSource):
    label = "manual"

    def decide(self, request: dict[str, Any]) -> dict[str, Any] | None:
        return None


class OneShotApprovals(ApprovalSource):
    """Answers exactly the pending checkpoint(s) once (used by ``agentic resume --decision ...``)."""

    label = "cli"

    def __init__(self, decision: dict[str, Any]):
        self.decision = decision
        self.used = False

    def decide(self, request: dict[str, Any]) -> dict[str, Any] | None:
        if self.used:
            return None
        self.used = True
        return {**self.decision, "source": "cli"}


class ScenarioApprovals(ApprovalSource):
    label = "pre-recorded"

    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.counts: dict[str, int] = {}

    def decide(self, request: dict[str, Any]) -> dict[str, Any] | None:
        gate = request["gate"]
        n = self.counts.get(gate, 0)
        self.counts[gate] = n + 1
        if request["type"] == "clarification":
            rounds = self.scenario.clarifications
            if n < len(rounds):
                return {**rounds[n], "source": "pre-recorded scenario decision"}
            return None
        if request["type"] == "safe_stop":
            recorded = self.scenario.approvals.get("safe_stop") or [
                {"action": "continue", "decision": "approve", "approver": "on-call-operator"}]
            return {**recorded[min(n, len(recorded) - 1)], "source": "pre-recorded scenario decision"}
        recorded = self.scenario.approvals.get(gate, [])
        if n < len(recorded):
            return {**recorded[n], "source": "pre-recorded scenario decision"}
        return None


class InteractiveApprovals(ApprovalSource):
    label = "interactive"

    def __init__(self, approver: str):
        self.approver = approver

    def decide(self, request: dict[str, Any]) -> dict[str, Any] | None:
        print("\n" + "=" * 78)
        print(f"HUMAN CHECKPOINT: {request['gate']}")
        print(json.dumps({k: v for k, v in request.items() if k not in ("type", "gate")}, indent=2)[:6000])
        if request["type"] == "clarification":
            answers = {}
            for q in request.get("questions", []):
                reply = input(f"{q['question']}\n  [enter = '{q['proposed_default']}'] > ").strip()
                answers[q["term"]] = reply or q["proposed_default"]
            return {"decision": "approve", "approver": self.approver, "answers": answers, "source": "interactive"}
        if request["type"] == "safe_stop":
            action = input("continue or abort? > ").strip() or "abort"
            return {"action": action, "decision": "approve", "approver": self.approver, "source": "interactive"}
        options = "/".join(request.get("options", []))
        decision = input(f"Decision ({options}) > ").strip()
        comment = input("Comment > ").strip()
        ack = bool(request.get("findings_requiring_acknowledgement")) and input(
            "Acknowledge findings that require approval? (y/N) > ").strip().lower() == "y"
        return {"decision": decision, "approver": self.approver, "comment": comment,
                "acknowledged_findings": ack, "source": "interactive"}
