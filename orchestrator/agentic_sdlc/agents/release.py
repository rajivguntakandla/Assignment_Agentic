from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime

from ..metrics import write_run_metrics
from ..report import render_summary
from .base import Agent, AgentContext, AgentResult


class ReleaseAgent(Agent):
    """Packages the reviewable outcome: patch, docs, release notes, traceability and the engineering summary.

    Promotion into the real project only happens with ``--promote`` AND an approval that explicitly authorises it.
    """

    name = "release"

    def run(self, ctx: AgentContext) -> AgentResult:
        rt = ctx.rt
        docs = ctx.inputs["documentation"]
        ws = rt.workspace

        # Bring the docs produced in parallel into the workspace so they ship in the same patch.
        doc_target = ws.root / docs["feature_doc_path"]
        doc_target.parent.mkdir(parents=True, exist_ok=True)
        doc_target.write_text(docs["feature_doc"])
        changelog = ws.root / "CHANGELOG.md"
        stamp = datetime.now(UTC).date().isoformat()
        previous = changelog.read_text() if changelog.exists() else "# Changelog\n"
        changelog.write_text(f"{previous.rstrip()}\n\n## {stamp} - {rt.scenario.title}\n\n{docs['changelog_entry']}\n")

        patch, diffs = ws.diff()
        out = rt.run_dir / "release"
        out.mkdir(exist_ok=True)
        (out / "change.patch").write_text(patch)
        traceability = self._traceability(ctx)
        (out / "traceability.json").write_text(json.dumps(traceability, indent=2))
        (out / "RELEASE_NOTES.md").write_text(f"# {rt.scenario.title}\n\n{docs['changelog_entry']}\n\n"
                                               f"Files changed: {len(diffs)}\n")
        metrics = write_run_metrics(rt.run_dir, rt.run_id)
        summary_path = out / "ENGINEERING_SUMMARY.md"
        summary_path.write_text(render_summary(ctx.state, rt, metrics, traceability, final=True))

        promoted = False
        approval = ctx.state.get("approvals", {}).get("release_approval", {})
        if rt.settings.promote and approval.get("promote"):
            for d in diffs:
                src, dst = ws.root / d.path, rt.settings.target_project / d.path
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            promoted = True
            rt.audit.record("promoted", actor=f"human:{approval.get('approver')}", stage=ctx.stage,
                            data={"files": [d.path for d in diffs], "target": str(rt.settings.target_project)})

        artifact = {"patch_path": str(out / "change.patch"), "summary_path": str(summary_path),
                    "release_notes": str(out / "RELEASE_NOTES.md"), "files": [d.path for d in diffs],
                    "promoted": promoted, "traceability": traceability}
        return AgentResult(artifact, [(f"Release package with {len(diffs)} file(s)"
                                       + ("; promoted to target project" if promoted else "; not promoted"),
                                       "human-approved; patch is the reviewable outcome")])

    @staticmethod
    def _traceability(ctx: AgentContext) -> list[dict]:
        spec, plan = ctx.inputs["requirements"], ctx.inputs["planning"]
        impl = ctx.inputs["implementation"]
        changes = json.loads((ctx.rt.run_dir / "artifacts" / f"changeset-r{impl['round']}.json").read_text())
        rows = []
        for fr in spec["functional_requirements"]:
            tasks = [t for t in plan["tasks"] if fr["id"] in t.get("requirement_ids", [])]
            task_ids = {t["id"] for t in tasks}
            files = sorted({c["path"] for c in changes["changes"] if c["task_id"] in task_ids})
            rows.append({"requirement": fr["id"], "text": fr["text"], "tasks": sorted(task_ids), "files": files,
                         "tests": [f for f in files if f.startswith("src/test/")],
                         "verified": ctx.inputs["verification"]["passed"]})
        return rows
