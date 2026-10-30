"""Command-line interface: ``agentic run | resume | status | stop | audit | metrics | graph | demo``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import dag
from .approvals import InteractiveApprovals, ManualApprovals, OneShotApprovals, ScenarioApprovals
from .audit import AuditLog
from .metrics import aggregate, to_prometheus
from .runner import Outcome, RunCrashed, get_state, resume, start
from .safety import SafeStopController
from .scenario import adhoc_scenario, load_scenario
from .settings import Settings, default_home


def _settings(args: argparse.Namespace) -> Settings:
    s = Settings(home=Path(args.home) if args.home else default_home())
    if getattr(args, "llm", None):
        s.llm_mode = args.llm
    for fault in getattr(args, "fault", None) or []:
        key, _, count = fault.partition("=")
        s.faults[key] = int(count or 1)
    if getattr(args, "promote", False):
        s.promote = True
    if getattr(args, "verify_cmd", None):
        s.verify_command = args.verify_cmd.split()
    return s


def _print_outcome(o: Outcome) -> int:
    print(f"\nrun {o.run_id}: {o.status.upper()}")
    if o.pending:
        for p in o.pending:
            print(f"  waiting for human: {p['gate']} - {p.get('summary', '')}")
        print(f"  decide with: agentic resume {o.run_id} --decision approve --approver <you> [--comment ...]")
    if o.halt:
        print(f"  halted at {o.halt['stage']} ({o.halt['code']}): {o.halt['reason']}")
    if o.metrics:
        keys = ("stage_success_rate", "retries", "rollbacks", "fallbacks", "replans", "mttr_s", "e2e_latency_s")
        print("  metrics: " + ", ".join(f"{k}={o.metrics.get(k)}" for k in keys))
    return {"completed": 0, "paused": 3}.get(o.status, 2)


def cmd_run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    scenario = adhoc_scenario(args.requirement, args.kind) if args.requirement else load_scenario(args.scenario)
    approvals = {"auto": lambda: ScenarioApprovals(scenario), "manual": ManualApprovals,
                 "interactive": lambda: InteractiveApprovals(args.approver)}[args.approvals]()
    try:
        return _print_outcome(start(scenario, settings, approvals, args.run_id))
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RunCrashed as exc:
        print(str(exc), file=sys.stderr)
        return 4


def cmd_resume(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if args.decision:
        decision = {"decision": args.decision, "approver": args.approver, "comment": args.comment or "",
                    "acknowledged_findings": args.acknowledge, "promote": args.promote, "action": args.action}
        if args.answer:
            decision["answers"] = dict(a.split("=", 1) for a in args.answer)
        if args.accept_defaults:
            decision["accept_defaults"] = True
        approvals = OneShotApprovals(decision)
    elif args.approvals == "auto":
        from .runner import load_runtime
        approvals = ScenarioApprovals(load_runtime(args.run_id, settings).scenario)
    elif args.approvals == "interactive":
        approvals = InteractiveApprovals(args.approver)
    else:
        approvals = ManualApprovals()
    try:
        return _print_outcome(resume(args.run_id, settings, approvals))
    except RunCrashed as exc:
        print(str(exc), file=sys.stderr)
        return 4


def cmd_status(args: argparse.Namespace) -> int:
    state, pending = get_state(args.run_id, _settings(args))
    print(f"run {args.run_id}  status={state.get('status')}  rework={state.get('rework_count', 0)}")
    print("path: " + " -> ".join(state.get("path", [])))
    for name in dag.topological_order():
        rec = state.get("stages", {}).get(name)
        if rec:
            print(f"  {name:18} {rec.get('status'):10} {rec.get('provider', '')} {rec.get('duration_s', '')}")
    for p in pending:
        print(f"  PENDING: {p['gate']} ({p['type']}) - {p.get('summary', '')}")
    if state.get("halt"):
        print(f"  HALT: {state['halt']}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    SafeStopController(_settings(args).run_dir(args.run_id)).request(args.reason)
    print(f"safe-stop requested for {args.run_id}; it pauses at the next stage boundary")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    path = _settings(args).run_dir(args.run_id) / "audit.jsonl"
    if args.action == "verify":
        result = AuditLog.verify(path)
        print(f"audit chain {'VALID' if result.ok else 'INVALID'}: {result.records} records"
              + (f" - {result.error}" if result.error else ""))
        return 0 if result.ok else 1
    for e in AuditLog(path, args.run_id).events():
        print(f"{e['seq']:4} {e['ts'][11:23]} {e['event']:20} {e.get('stage') or '':16} {e['actor']:24} "
              f"{json.dumps(e['data'])[:110]}")
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    agg = aggregate(_settings(args).home)
    print(to_prometheus(agg) if args.prometheus else json.dumps({k: v for k, v in agg.items() if k != "per_run"}
                                                                 | ({"per_run": agg["per_run"]} if args.verbose
                                                                    else {}), indent=2))
    return 0


def cmd_graph(_: argparse.Namespace) -> int:
    print(dag.mermaid())
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import run_demo
    return run_demo(_settings(args), Path(args.evidence_dir) if args.evidence_dir else None)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentic", description="Governed agentic SDLC orchestrator")
    p.add_argument("--home", help="runs directory (default: orchestrator/runs or $AGENTIC_HOME)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="start a run")
    r.add_argument("--scenario", default="greenfield", help="greenfield | brownfield | ambiguous | path.yaml")
    r.add_argument("--requirement", help="ad-hoc requirement text instead of a scenario")
    r.add_argument("--kind", default="brownfield", choices=["greenfield", "brownfield", "ambiguous"])
    r.add_argument("--llm", choices=["auto", "claude", "offline"], default="auto")
    r.add_argument("--approvals", choices=["auto", "manual", "interactive"], default="manual",
                   help="auto = pre-recorded scenario decisions; manual = pause and resume later")
    r.add_argument("--approver", default="")
    r.add_argument("--fault", action="append", help="inject a fault, e.g. llm_transient:planning=2")
    r.add_argument("--run-id")
    r.add_argument("--promote", action="store_true", help="allow an approved release to update the real project")
    r.add_argument("--verify-cmd", help="override the verification command (allow-listed binaries only)")
    r.set_defaults(fn=cmd_run)

    rs = sub.add_parser("resume", help="resume a paused or crashed run")
    rs.add_argument("run_id")
    rs.add_argument("--decision", choices=["approve", "reject", "request_changes", "abort"])
    rs.add_argument("--approver", default="")
    rs.add_argument("--comment")
    rs.add_argument("--acknowledge", action="store_true", help="acknowledge findings that require approval")
    rs.add_argument("--promote", action="store_true")
    rs.add_argument("--action", default="continue", choices=["continue", "abort"], help="for safe-stop pauses")
    rs.add_argument("--answer", action="append", help="clarification answer term=text")
    rs.add_argument("--accept-defaults", action="store_true")
    rs.add_argument("--approvals", choices=["auto", "manual", "interactive"], default="manual")
    rs.set_defaults(fn=cmd_resume)

    st = sub.add_parser("status")
    st.add_argument("run_id")
    st.set_defaults(fn=cmd_status)

    sp = sub.add_parser("stop", help="request a safe stop")
    sp.add_argument("run_id")
    sp.add_argument("--reason", default="operator requested safe stop")
    sp.set_defaults(fn=cmd_stop)

    au = sub.add_parser("audit")
    au.add_argument("action", choices=["verify", "show"])
    au.add_argument("run_id")
    au.set_defaults(fn=cmd_audit)

    m = sub.add_parser("metrics")
    m.add_argument("--prometheus", action="store_true")
    m.set_defaults(fn=cmd_metrics)

    sub.add_parser("graph", help="print the SDLC graph as mermaid").set_defaults(fn=cmd_graph)

    d = sub.add_parser("demo", help="run all scenarios + resilience drill, write evidence")
    d.add_argument("--llm", choices=["auto", "claude", "offline"], default="offline")
    d.add_argument("--evidence-dir")
    d.set_defaults(fn=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
