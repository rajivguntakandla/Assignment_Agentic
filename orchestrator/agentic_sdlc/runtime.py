"""Per-run runtime services (not part of checkpointed state; rebuilt identically on resume)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .audit import AuditLog
from .executor import CommandRunner
from .llm import ClaudeChatModel, LLMRouter, ReplayChatModel, claude_credentials_available
from .policy import PolicyEngine
from .resilience import FaultInjector
from .safety import BudgetGuard, SafeStopController
from .scenario import Scenario
from .settings import Settings
from .workspace import Workspace


@dataclass
class Runtime:
    run_id: str
    run_dir: Path
    settings: Settings
    scenario: Scenario
    audit: AuditLog
    llm: LLMRouter
    policy: PolicyEngine
    workspace: Workspace
    runner: CommandRunner
    faults: FaultInjector
    budget: BudgetGuard
    safe_stop: SafeStopController

    def artifact_path(self, name: str) -> Path:
        path = self.run_dir / "artifacts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def save_artifact(self, name: str, data: object) -> Path:
        path = self.artifact_path(name)
        path.write_text(data if isinstance(data, str) else json.dumps(data, indent=2, default=str))
        return path


def build_runtime(run_id: str, settings: Settings, scenario: Scenario) -> Runtime:
    run_dir = settings.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    audit = AuditLog(run_dir / "audit.jsonl", run_id)
    faults = FaultInjector(settings.faults, run_dir / "faults-remaining.json")
    budget = BudgetGuard(settings.budget)

    providers = []
    use_claude = settings.llm_mode == "claude" or (settings.llm_mode == "auto" and claude_credentials_available())
    if use_claude:
        providers.append(("claude", ClaudeChatModel(model=settings.claude_model, effort=settings.claude_effort)))
    if scenario.recordings:
        providers.append(("replay", ReplayChatModel(recordings=scenario.recordings)))

    llm = LLMRouter(providers, audit, budget, faults, settings.llm_max_retries, settings.llm_backoff_base_s)
    workspace = Workspace(run_dir, settings.target_project)
    workspace.ensure()
    return Runtime(run_id, run_dir, settings, scenario, audit, llm, PolicyEngine(), workspace,
                   CommandRunner(settings.verify_timeout_s), faults, budget, SafeStopController(run_dir))
