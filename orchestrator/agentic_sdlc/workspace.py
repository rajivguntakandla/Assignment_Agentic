"""Isolated working copy of the target project with snapshot / rollback and diffing.

Agents never write to the real project. All changes land in ``<run>/workspace``; the reviewable outcome is a
unified diff. Promotion back to the real project is a separate, human-approved action.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

from .models import ChangeSet, FileChange
from .policy import FileDiff
from .resilience import OutputValidationError

SKIP_DIRS = {"target", "data", ".idea", "node_modules"}
IGNORE = shutil.ignore_patterns(*SKIP_DIRS, "*.iml", ".DS_Store")


def walk_files(root: Path) -> Iterator[str]:
    """Relative file paths, pruning build output (safe while a build runs concurrently in the tree)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name != ".DS_Store" and not name.endswith(".iml"):
                yield str((Path(dirpath) / name).relative_to(root))


class Workspace:
    def __init__(self, run_dir: Path, source: Path):
        self.source = source
        self.root = run_dir / "workspace"
        self.baseline = run_dir / "snapshots" / "baseline"
        self.snapshots = run_dir / "snapshots"

    def ensure(self) -> None:
        if not self.baseline.exists():
            shutil.copytree(self.source, self.baseline, ignore=IGNORE)
        if not self.root.exists():
            shutil.copytree(self.baseline, self.root)

    # ---------------------------------------------------------------------------------------- snapshots
    def snapshot(self, label: str) -> str:
        target = self.snapshots / label
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(self.root, target, ignore=IGNORE)
        return self.tree_hash(target)

    def restore(self, label: str) -> str:
        source = self.snapshots / label
        if not source.exists():
            raise FileNotFoundError(f"snapshot {label!r} does not exist")
        shutil.rmtree(self.root)
        shutil.copytree(source, self.root)
        return self.tree_hash(self.root)

    @staticmethod
    def tree_hash(root: Path) -> str:
        h = hashlib.sha256()
        for rel in walk_files(root):
            h.update(rel.encode())
            h.update((root / rel).read_bytes())
        return h.hexdigest()[:16]

    # ------------------------------------------------------------------------------------------- files
    def files(self) -> set[str]:
        return set(walk_files(self.root))

    def read(self, rel: str) -> str:
        return self._resolve(rel).read_text()

    def _resolve(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise OutputValidationError(f"path escapes workspace: {rel}")
        return path

    def apply(self, changeset: ChangeSet) -> None:
        """Apply all changes or none (validated in memory first, then written)."""
        pending: dict[Path, str] = {}
        for change in changeset.changes:
            path = self._resolve(change.path)
            pending[path] = self._render(change, path, pending.get(path))
        for path, content in pending.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    @staticmethod
    def _render(change: FileChange, path: Path, staged: str | None) -> str:
        if change.action == "create":
            if change.content is None:
                raise OutputValidationError(f"{change.path}: 'create' requires content")
            return change.content
        current = staged if staged is not None else (path.read_text() if path.exists() else None)
        if current is None:
            raise OutputValidationError(f"{change.path}: cannot modify a missing file")
        if change.content is not None and not change.edits:
            return change.content
        if not change.edits:
            raise OutputValidationError(f"{change.path}: 'modify' requires edits or content")
        for i, edit in enumerate(change.edits, start=1):
            occurrences = current.count(edit.search)
            if occurrences != 1:
                raise OutputValidationError(
                    f"{change.path}: edit {i} search text must match exactly once (matched {occurrences}x)")
            current = current.replace(edit.search, edit.replace, 1)
        return current

    # -------------------------------------------------------------------------------------------- diff
    def diff(self) -> tuple[str, list[FileDiff]]:
        """Unified diff of workspace vs baseline, plus per-file added lines for policy scanning."""
        patch: list[str] = []
        diffs: list[FileDiff] = []
        base_files = set(walk_files(self.baseline))
        for rel in sorted(base_files | self.files()):
            before_p, after_p = self.baseline / rel, self.root / rel
            before = before_p.read_text(errors="ignore").splitlines(keepends=True) if before_p.exists() else []
            after = after_p.read_text(errors="ignore").splitlines(keepends=True) if after_p.exists() else []
            if before == after:
                continue
            udiff = list(difflib.unified_diff(before, after, fromfile=f"a/{rel}" if before_p.exists() else "/dev/null",
                                              tofile=f"b/{rel}" if after_p.exists() else "/dev/null"))
            patch.extend(line if line.endswith("\n") else line + "\n" for line in udiff)
            added: list[tuple[int, str]] = []
            removed = 0
            for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, before, after).get_opcodes():
                if tag in ("replace", "insert"):
                    added.extend((j + 1, after[j].rstrip("\n")) for j in range(j1, j2))
                if tag in ("replace", "delete"):
                    removed += _i2 - _i1
            diffs.append(FileDiff(rel, before_p.exists(), added, removed))
        return "".join(patch), diffs
