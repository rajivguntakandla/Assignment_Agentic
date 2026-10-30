"""Reliability metrics, derived purely from the audit log.

Deriving metrics from the audit trail (instead of separate counters) means every number is reproducible and
explainable from recorded evidence.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from .audit import read_events


def _ts(event: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(event["ts"])


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[idx], 3)


def compute_run_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        by_type.setdefault(e["event"], []).append(e)

    completed = by_type.get("stage_completed", [])
    failed = by_type.get("stage_failed", [])
    executions = len(completed) + len(failed)

    # MTTR: time from a failure to the next successful completion of the same stage.
    recoveries: list[float] = []
    for f in failed:
        later = [c for c in completed if c["stage"] == f["stage"] and c["seq"] > f["seq"]]
        if later:
            recoveries.append((_ts(later[0]) - _ts(f)).total_seconds())

    # Crash recovery (durable resume) also counts towards MTTR: crash -> first stage completion afterwards.
    for crash in by_type.get("run_crashed", []):
        later = [c for c in completed if c["seq"] > crash["seq"]]
        if later:
            recoveries.append((_ts(later[0]) - _ts(crash)).total_seconds())

    # Human wait time is excluded from "active" latency: agents should not be blamed for review time.
    human_wait = 0.0
    requests = {e["data"].get("gate_key"): e for e in by_type.get("approval_requested", [])}
    for d in by_type.get("approval_decided", []):
        r = requests.get(d["data"].get("gate_key"))
        if r:
            human_wait += (_ts(d) - _ts(r)).total_seconds()

    starts = by_type.get("run_started", [])
    ends = by_type.get("run_completed", []) + by_type.get("run_halted", [])
    e2e = (_ts(ends[-1]) - _ts(starts[0])).total_seconds() if starts and ends else None

    stage_latency: dict[str, list[float]] = {}
    for c in completed:
        stage_latency.setdefault(c["stage"], []).append(c["data"].get("duration_s", 0.0))

    llm = by_type.get("llm_call", [])
    llm_latencies = [e["data"].get("latency_s", 0.0) for e in llm]
    providers: dict[str, int] = {}
    for e in llm:
        if e["data"].get("ok"):
            providers[e["data"].get("provider", "?")] = providers.get(e["data"].get("provider", "?"), 0) + 1

    retries = by_type.get("retry", [])
    retry_scopes: dict[str, int] = {}
    for r in retries:
        retry_scopes[r["data"].get("scope", "?")] = retry_scopes.get(r["data"].get("scope", "?"), 0) + 1

    status = ends[-1]["data"].get("status") if ends else "in_progress"
    return {
        "run_status": status,
        "stage_executions": executions,
        "stage_success_rate": round(len(completed) / executions, 3) if executions else None,
        "stage_failures": len(failed),
        "retries": len(retries),
        "retries_by_scope": retry_scopes,
        "retry_frequency": round(len(retries) / executions, 3) if executions else 0.0,
        "rollbacks": len(by_type.get("rollback", [])),
        "rollback_frequency": round(len(by_type.get("rollback", [])) / executions, 3) if executions else 0.0,
        "fallbacks": len(by_type.get("fallback", [])),
        "replans": len(by_type.get("replan", [])),
        "safe_stops": len(by_type.get("safe_stop_requested", [])),
        "approvals": len(by_type.get("approval_decided", [])),
        "policy_findings": sum(e["data"].get("total", 0) for e in by_type.get("policy_evaluated", [])),
        "mttr_s": round(statistics.mean(recoveries), 3) if recoveries else None,
        "recoveries": len(recoveries),
        "e2e_latency_s": round(e2e, 3) if e2e is not None else None,
        "human_wait_s": round(human_wait, 3),
        "active_latency_s": round(e2e - human_wait, 3) if e2e is not None else None,
        "llm_calls": len(llm),
        "llm_calls_by_provider": providers,
        "llm_tokens": {"input": sum(e["data"].get("input_tokens", 0) for e in llm),
                       "output": sum(e["data"].get("output_tokens", 0) for e in llm)},
        "llm_latency_p50_s": _pct(llm_latencies, 0.5),
        "llm_latency_p95_s": _pct(llm_latencies, 0.95),
        "stage_latency_p50_s": {k: _pct(v, 0.5) for k, v in sorted(stage_latency.items())},
    }


def aggregate(home: Path) -> dict[str, Any]:
    runs: dict[str, dict[str, Any]] = {}
    for audit in sorted(home.glob("*/audit.jsonl")):
        runs[audit.parent.name] = compute_run_metrics(read_events(audit))
    finished = [m for m in runs.values() if m["run_status"] != "in_progress"]
    succeeded = [m for m in finished if m["run_status"] == "completed"]
    mttrs = [m["mttr_s"] for m in runs.values() if m["mttr_s"] is not None]
    e2e = [m["e2e_latency_s"] for m in finished if m["e2e_latency_s"] is not None]
    executions = sum(m["stage_executions"] for m in runs.values())
    return {
        "runs": len(runs),
        "runs_finished": len(finished),
        "run_success_rate": round(len(succeeded) / len(finished), 3) if finished else None,
        "stage_success_rate": (
            round(sum(m["stage_executions"] - m["stage_failures"] for m in runs.values()) / executions, 3)
            if executions else None
        ),
        "retry_frequency": round(sum(m["retries"] for m in runs.values()) / executions, 3) if executions else 0.0,
        "rollback_frequency": round(sum(m["rollbacks"] for m in runs.values()) / executions, 3) if executions else 0.0,
        "fallbacks": sum(m["fallbacks"] for m in runs.values()),
        "mttr_s": round(statistics.mean(mttrs), 3) if mttrs else None,
        "e2e_latency_p50_s": _pct(e2e, 0.5),
        "e2e_latency_p95_s": _pct(e2e, 0.95),
        "per_run": runs,
    }


def to_prometheus(agg: dict[str, Any]) -> str:
    lines = []

    def gauge(name: str, value: Any, help_text: str) -> None:
        if value is None:
            return
        lines.append(f"# HELP agentic_{name} {help_text}")
        lines.append(f"# TYPE agentic_{name} gauge")
        lines.append(f"agentic_{name} {value}")

    gauge("runs_total", agg["runs"], "Runs recorded")
    gauge("run_success_rate", agg["run_success_rate"], "Finished runs that completed successfully")
    gauge("stage_success_rate", agg["stage_success_rate"], "Stage executions that succeeded")
    gauge("retry_frequency", agg["retry_frequency"], "Retries per stage execution")
    gauge("rollback_frequency", agg["rollback_frequency"], "Rollbacks per stage execution")
    gauge("fallbacks_total", agg["fallbacks"], "Provider/agent fallbacks")
    gauge("mttr_seconds", agg["mttr_s"], "Mean time from stage failure to recovery")
    gauge("e2e_latency_p50_seconds", agg["e2e_latency_p50_s"], "Median end-to-end run latency")
    return "\n".join(lines) + "\n"


def write_run_metrics(run_dir: Path, run_id: str) -> dict[str, Any]:
    metrics = compute_run_metrics(read_events(run_dir / "audit.jsonl"))
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    return metrics
