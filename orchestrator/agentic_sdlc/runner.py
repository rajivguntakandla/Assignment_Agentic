"""Run lifecycle: start, drive through human checkpoints, resume after pause/crash, finalise."""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .approvals import ApprovalSource
from .engine import Engine
from .metrics import write_run_metrics
from .runtime import Runtime, build_runtime
from .scenario import Scenario
from .settings import Settings, display_path


class RunCrashed(Exception):  # noqa: N818
    pass


@dataclass
class Outcome:
    run_id: str
    status: str  # completed | halted | paused
    pending: list[dict[str, Any]] = field(default_factory=list)
    halt: dict[str, Any] | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


@contextmanager
def _checkpointer(settings: Settings):
    settings.home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.checkpoint_db, check_same_thread=False)
    try:
        yield SqliteSaver(conn)
    finally:
        conn.close()


def _config(run_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": run_id}, "recursion_limit": 120}


def new_run_id(scenario: Scenario) -> str:
    return f"{scenario.id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(2)}"


def start(scenario: Scenario, settings: Settings, approvals: ApprovalSource, run_id: str | None = None) -> Outcome:
    run_id = run_id or new_run_id(scenario)
    run_dir = settings.run_dir(run_id)
    if run_dir.exists():
        raise FileExistsError(f"run {run_id!r} already exists; resume it or choose another --run-id")
    run_dir.mkdir(parents=True)
    with _checkpointer(settings) as saver:
        saver.delete_thread(run_id)  # never inherit state from an orphaned checkpoint thread
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_id, "created": datetime.now(UTC).isoformat(), "scenario": scenario.model_dump(mode="json"),
        "settings": settings.model_dump(mode="json")}, indent=2))
    rt = build_runtime(run_id, settings, scenario)
    rt.audit.record("run_started", data={
        "scenario": scenario.id, "kind": scenario.kind, "llm_providers": rt.llm.provider_names,
        "approvals": approvals.label, "budget": settings.budget.model_dump(), "faults": settings.faults,
        "target": display_path(settings.target_project),
        "baseline_hash": rt.workspace.tree_hash(rt.workspace.baseline)})
    initial = {"run_id": run_id, "scenario": {"id": scenario.id, "kind": scenario.kind, "title": scenario.title},
               "artifacts": {}, "stages": {}, "approvals": {}, "lineage": [], "path": [], "rework_count": 0,
               "clarification_rounds": 0, "design_revisions": 0, "status": "running"}
    return _drive(rt, approvals, initial)


def load_runtime(run_id: str, home_settings: Settings) -> Runtime:
    meta = json.loads((home_settings.run_dir(run_id) / "run.json").read_text())
    settings = Settings.model_validate(meta["settings"])
    scenario = Scenario.model_validate(meta["scenario"])
    return build_runtime(run_id, settings, scenario)


def resume(run_id: str, home_settings: Settings, approvals: ApprovalSource) -> Outcome:
    rt = load_runtime(run_id, home_settings)
    rt.audit.record("run_resumed", data={"approvals": approvals.label})
    return _drive(rt, approvals, None)


def _drive(rt: Runtime, approvals: ApprovalSource, payload: Any) -> Outcome:
    with _checkpointer(rt.settings) as saver:
        engine = Engine(rt, saver)
        config = _config(rt.run_id)
        while True:
            try:
                engine.graph.invoke(payload, config)
            except Exception as exc:
                rt.audit.record("run_crashed", data={"error": f"{type(exc).__name__}: {exc}"[:500]})
                raise RunCrashed(f"run {rt.run_id} crashed: {exc}. State is checkpointed; "
                                 f"continue with: agentic resume {rt.run_id}") from exc
            snapshot = engine.graph.get_state(config)
            if snapshot.interrupts:
                answers: dict[str, Any] = {}
                pending: list[dict[str, Any]] = []
                for intr in snapshot.interrupts:
                    decision = approvals.decide(intr.value)
                    if decision is None:
                        pending.append({"id": intr.id, **intr.value})
                    else:
                        answers[intr.id] = decision
                if pending and not answers:
                    rt.audit.record("run_paused", data={"pending": [p["gate"] for p in pending]},
                                    dedupe_key=f"paused:{','.join(sorted(p['gate_key'] for p in pending))}")
                    return Outcome(rt.run_id, "paused", pending=pending)
                payload = Command(resume=answers)
                continue
            return _finalize(rt, engine, snapshot.values)


def _finalize(rt: Runtime, engine: Engine, state: dict[str, Any]) -> Outcome:
    halted = state.get("status") == "halted" or bool(state.get("halt"))
    if halted:
        rt.audit.record("run_halted", data={"status": "halted", **(state.get("halt") or {})},
                        dedupe_key="final")
    else:
        rt.audit.record("run_completed", data={"status": "completed",
                                               "files": state["artifacts"]["release"]["files"]}, dedupe_key="final")
    metrics = write_run_metrics(rt.run_dir, rt.run_id)
    engine.write_summary(state, metrics, final=not halted)
    return Outcome(rt.run_id, "halted" if halted else "completed", halt=state.get("halt"), metrics=metrics)


def get_state(run_id: str, home_settings: Settings) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rt = load_runtime(run_id, home_settings)
    with _checkpointer(rt.settings) as saver:
        snap = Engine(rt, saver).graph.get_state(_config(run_id))
        return dict(snap.values), [{"id": i.id, **i.value} for i in snap.interrupts]
