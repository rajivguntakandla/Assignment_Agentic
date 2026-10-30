"""Scenario definitions: the requirement, recorded human decisions for unattended runs, and recorded LLM
responses for offline replay."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .settings import ORCHESTRATOR_DIR

SCENARIO_DIR = ORCHESTRATOR_DIR / "scenarios"


class Scenario(BaseModel):
    id: str
    kind: Literal["greenfield", "brownfield", "ambiguous"]
    title: str
    requirement: str
    # Pre-recorded human decisions, consumed in order per gate when running with --approvals auto.
    approvals: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    clarifications: list[dict[str, Any]] = Field(default_factory=list)
    recordings: dict[str, list[Any]] = Field(default_factory=dict)


def load_scenario(name_or_path: str) -> Scenario:
    path = Path(name_or_path)
    if not path.suffix:
        path = SCENARIO_DIR / f"{name_or_path}.yaml"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in SCENARIO_DIR.glob("*.yaml")))
        raise FileNotFoundError(f"scenario {name_or_path!r} not found (available: {available})")
    return Scenario.model_validate(yaml.safe_load(path.read_text()))


def adhoc_scenario(requirement: str, kind: str) -> Scenario:
    return Scenario(id="adhoc", kind=kind, title=requirement[:60], requirement=requirement)
