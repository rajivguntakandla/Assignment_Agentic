"""LangGraph state. Fields written by parallel branches use merge reducers."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow merge where a ``None`` value deletes the key (used to invalidate stale artifacts)."""
    out = dict(left or {})
    for k, v in (right or {}).items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out


def keep_first(left: Any, right: Any) -> Any:
    """``halt`` may be written by several parallel branches in one step; the first reason wins (halt is terminal)."""
    return left if left else right


class RunState(TypedDict, total=False):
    run_id: str
    scenario: dict[str, Any]
    artifacts: Annotated[dict[str, Any], merge]
    stages: Annotated[dict[str, Any], merge]
    approvals: Annotated[dict[str, Any], merge]
    lineage: Annotated[list[dict[str, Any]], operator.add]
    path: Annotated[list[str], operator.add]
    clarifications: dict[str, str]
    clarification_rounds: int
    design_feedback: list[str]
    design_revisions: int
    rework_feedback: list[str]
    rework_count: int
    halt: Annotated[dict[str, Any] | None, keep_first]
    status: str
