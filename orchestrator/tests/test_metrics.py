from agentic_sdlc.metrics import aggregate, compute_run_metrics, to_prometheus


def ev(seq, event, ts, stage=None, **data):
    return {"seq": seq, "event": event, "ts": f"2026-01-01T00:00:{ts:02d}+00:00", "stage": stage, "data": data}


def test_run_metrics_from_audit_events():
    events = [
        ev(1, "run_started", 0),
        ev(2, "stage_completed", 1, "requirements", duration_s=1.0),
        ev(3, "stage_failed", 2, "verification"),
        ev(4, "retry", 2, scope="rework"),
        ev(5, "rollback", 2),
        ev(6, "approval_requested", 3, gate_key="g1"),
        ev(7, "approval_decided", 8, gate_key="g1"),
        ev(8, "stage_completed", 12, "verification", duration_s=2.0),
        ev(9, "llm_call", 12, provider="replay", ok=True, latency_s=0.1),
        ev(10, "run_completed", 20, status="completed"),
    ]
    m = compute_run_metrics(events)
    assert m["run_status"] == "completed"
    assert m["stage_success_rate"] == round(2 / 3, 3)
    assert m["mttr_s"] == 10.0
    assert m["retries_by_scope"] == {"rework": 1}
    assert m["rollbacks"] == 1
    assert m["e2e_latency_s"] == 20.0
    assert m["human_wait_s"] == 5.0 and m["active_latency_s"] == 15.0
    assert m["llm_calls_by_provider"] == {"replay": 1}


def test_aggregate_over_runs(tmp_path):
    import json

    for name, status in [("a", "completed"), ("b", "halted")]:
        (tmp_path / name).mkdir()
        evs = [ev(1, "run_started", 0), ev(2, "stage_completed", 1, "x"),
               ev(3, "run_completed" if status == "completed" else "run_halted", 2, status=status)]
        (tmp_path / name / "audit.jsonl").write_text("\n".join(json.dumps(e) for e in evs))
    agg = aggregate(tmp_path)
    assert agg["runs"] == 2 and agg["run_success_rate"] == 0.5
    assert "agentic_run_success_rate 0.5" in to_prometheus(agg)
