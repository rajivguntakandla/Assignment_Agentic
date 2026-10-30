"""Policy guardrails for security, compliance and change control.

Rules are data (``config/policies.yaml``), so governance can be tightened without code changes. Content rules
look only at *added* lines, so pre-existing code never blocks unrelated work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import FileChange, PolicyFinding
from .settings import PACKAGE_DIR

DEFAULT_POLICY_FILE = PACKAGE_DIR / "config" / "policies.yaml"
TEST_ANNOTATION = re.compile(r"@(Test|ParameterizedTest|RepeatedTest)\b")


@dataclass(frozen=True)
class FileDiff:
    path: str
    existed_before: bool
    added_lines: list[tuple[int, str]]
    removed_lines: int


def load_policies(path: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load((path or DEFAULT_POLICY_FILE).read_text())


class PolicyEngine:
    def __init__(self, policies: dict[str, Any] | None = None):
        self.p = policies or load_policies()
        cc = self.p["change_control"]
        self.allowed = tuple(cc["allowed_paths"]) + tuple(cc.get("approval_paths", []))
        self.protected = tuple(cc["protected_paths"])
        self.approval_paths = tuple(cc.get("approval_paths", []))
        self.immutable = tuple(cc.get("immutable_existing", []))
        self.security_sensitive = tuple(cc.get("security_sensitive", []))
        self.schema_paths = tuple(cc.get("schema_paths", []))
        self._content_rules = [
            (r, "security") for r in self.p["security"]["rules"]
        ] + [(r, "compliance") for r in self.p["compliance"]["rules"]]
        self._compiled = {r["id"]: re.compile(r["pattern"]) for r, _ in self._content_rules}
        self._secrets = [(s["id"], re.compile(s["pattern"])) for s in self.p["security"]["secrets"]]

    # ----------------------------------------------------------------- classification helpers (used by design)
    def is_security_sensitive(self, path: str) -> bool:
        return path.startswith(self.security_sensitive)

    def is_schema_path(self, path: str) -> bool:
        return path.startswith(self.schema_paths)

    # ----------------------------------------------------------------------------------- pre-apply checks
    def check_paths(self, changes: list[FileChange], existing: set[str]) -> list[PolicyFinding]:
        """Path-level change control. Runs *before* anything touches the workspace."""
        out: list[PolicyFinding] = []
        cc = self.p["change_control"]
        if len(changes) > cc["max_files_changed"]:
            out.append(PolicyFinding(rule="CC-001-change-size", category="change_control", severity="block",
                                     message=f"{len(changes)} files changed; limit is {cc['max_files_changed']}"))
        for ch in changes:
            if ch.path.startswith(self.protected):
                out.append(PolicyFinding(rule="CC-002-protected-path", category="change_control", severity="block",
                                         path=ch.path, message="Protected path may not be modified by agents"))
            elif not ch.path.startswith(self.allowed):
                out.append(PolicyFinding(rule="CC-003-path-allowlist", category="change_control", severity="block",
                                         path=ch.path, message="Path is outside the agent write allowlist"))
            if ch.path in existing and ch.path.startswith(self.immutable):
                out.append(PolicyFinding(rule="CC-004-immutable-migration", category="change_control",
                                         severity="block", path=ch.path,
                                         message="Applied migrations are immutable; add a new versioned migration"))
            if ch.path.startswith(self.approval_paths):
                out.append(PolicyFinding(rule="CC-005-approval-path", category="change_control",
                                         severity="require_approval", path=ch.path,
                                         message="Build/runtime configuration change needs explicit sign-off"))
            if ch.action == "create" and ch.path in existing:
                out.append(PolicyFinding(rule="CC-006-create-overwrite", category="change_control", severity="block",
                                         path=ch.path, message="'create' would overwrite an existing file"))
            if ch.action == "modify" and ch.path not in existing:
                out.append(PolicyFinding(rule="CC-007-modify-missing", category="change_control", severity="block",
                                         path=ch.path, message="'modify' targets a file that does not exist"))
        return out

    # ---------------------------------------------------------------------------------- post-apply checks
    def check_diff(self, diffs: list[FileDiff], planned_files: list[str], tests_before: int,
                   tests_after: int) -> list[PolicyFinding]:
        out: list[PolicyFinding] = []
        cc = self.p["change_control"]
        total = sum(len(d.added_lines) + d.removed_lines for d in diffs)
        if total > cc["max_lines_changed"]:
            out.append(PolicyFinding(rule="CC-008-diff-size", category="change_control", severity="block",
                                     message=f"{total} lines changed; limit is {cc['max_lines_changed']}"))
        if tests_after < tests_before:
            out.append(PolicyFinding(rule="CC-009-test-deletion", category="change_control", severity="block",
                                     message=f"Test count dropped from {tests_before} to {tests_after}"))
        main_changed = any(d.path.startswith("src/main/java/") for d in diffs)
        test_changed = any(d.path.startswith("src/test/java/") for d in diffs)
        if cc.get("require_tests_with_code") and main_changed and not test_changed:
            out.append(PolicyFinding(rule="CC-010-tests-required", category="change_control", severity="block",
                                     message="Production code changed without accompanying tests"))
        planned = set(planned_files)
        for d in diffs:
            if planned and d.path not in planned and not d.path.startswith(("src/test/", "docs/")):
                out.append(PolicyFinding(rule="CC-011-scope-creep", category="change_control",
                                         severity="require_approval", path=d.path,
                                         message="File changed outside the approved design scope"))
        for d in diffs:
            out.extend(self._scan_lines(d))
        return out

    def _scan_lines(self, d: FileDiff) -> list[PolicyFinding]:
        out: list[PolicyFinding] = []
        is_main = d.path.startswith("src/main/")
        for lineno, text in d.added_lines:
            for rule, category in self._content_rules:
                if rule.get("main_only") and not is_main:
                    continue
                if self._compiled[rule["id"]].search(text):
                    out.append(PolicyFinding(rule=rule["id"], category=category, severity=rule["severity"],
                                             path=d.path, line=lineno, message=rule["message"]))
            for rule_id, rx in self._secrets:
                if rx.search(text):
                    out.append(PolicyFinding(rule=rule_id, category="security", severity="block", path=d.path,
                                             line=lineno, message="Hard-coded secret or credential"))
        return out


def summarize(findings: list[PolicyFinding]) -> dict[str, int]:
    counts = {"block": 0, "require_approval": 0, "warn": 0}
    for f in findings:
        counts[f.severity] += 1
    counts["total"] = len(findings)
    return counts


def count_tests(root: Path) -> int:
    test_dir = root / "src" / "test"
    if not test_dir.exists():
        return 0
    return sum(len(TEST_ANNOTATION.findall(p.read_text(errors="ignore"))) for p in test_dir.rglob("*.java"))
