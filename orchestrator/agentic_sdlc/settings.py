"""Run settings. Persisted with each run so a resumed run rebuilds an identical runtime."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

PACKAGE_DIR = Path(__file__).resolve().parent
ORCHESTRATOR_DIR = PACKAGE_DIR.parent
REPO_ROOT = ORCHESTRATOR_DIR.parent


def display_path(path: Path | str) -> str:
    """Repository-relative path for reports and audit records (no user-specific absolute paths)."""
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def default_home() -> Path:
    return Path(os.environ.get("AGENTIC_HOME", ORCHESTRATOR_DIR / "runs"))


class Budget(BaseModel):
    """Hard limits that trigger a safe stop when exceeded (controlled autonomy)."""

    max_llm_calls: int = 60
    max_rework_cycles: int = 3
    max_design_revisions: int = 2
    max_clarification_rounds: int = 2
    max_wall_clock_s: int = 1800


class Settings(BaseModel):
    llm_mode: str = Field(default="auto", pattern="^(auto|claude|offline)$")
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "high"
    target_project: Path = REPO_ROOT / "url-shortener"
    home: Path = Field(default_factory=default_home)
    verify_command: list[str] = Field(default_factory=lambda: ["mvn", "-B", "-q", "test"])
    verify_timeout_s: int = 900
    llm_max_retries: int = 2
    llm_backoff_base_s: float = 0.5
    stage_max_attempts: int = 2
    promote: bool = False
    budget: Budget = Field(default_factory=Budget)
    faults: dict[str, int] = Field(default_factory=dict)

    def run_dir(self, run_id: str) -> Path:
        return self.home / run_id

    @property
    def checkpoint_db(self) -> Path:
        return self.home / "checkpoints.sqlite"
