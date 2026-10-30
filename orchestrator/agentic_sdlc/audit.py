"""Append-only, hash-chained audit log.

Each record carries the SHA-256 of its predecessor, so any edit, deletion or reordering of history is
detectable with :meth:`AuditLog.verify`. Secrets are redacted before they are written. Records also carry
trace/span identifiers so the log doubles as an execution trace.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("agentic.audit")

GENESIS = "0" * 64
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|password|secret|token)(\"?\s*[:=]\s*\"?)[^\s\",]{6,}"),
]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def redact(value: Any) -> Any:
    if isinstance(value, str):
        out = value
        for p in _SECRET_PATTERNS:
            out = p.sub(lambda m: (m.group(1) + m.group(2) + "[REDACTED]") if m.lastindex else "[REDACTED]", out)
        return out
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()[:16]


def read_events(path: Path) -> list[dict[str, Any]]:
    """Read-only access (metrics, reports); does not require or check the chain."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    records: int
    error: str | None = None


class AuditLog:
    def __init__(self, path: Path, run_id: str):
        self.path = path
        self.run_id = run_id
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._last_hash, self._seq, self._dedupe = self._load_tail()

    def _load_tail(self) -> tuple[str, int, set[str]]:
        last, seq, keys = GENESIS, 0, set()
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    last, seq = rec["hash"], rec["seq"]
                    if rec.get("dedupe_key"):
                        keys.add(rec["dedupe_key"])
        return last, seq, keys

    def record(
        self,
        event: str,
        *,
        actor: str = "orchestrator",
        stage: str | None = None,
        data: dict[str, Any] | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        dedupe_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Append one event. ``dedupe_key`` makes emission idempotent across LangGraph node re-execution."""
        with self._lock:
            if dedupe_key and dedupe_key in self._dedupe:
                return None
            self._seq += 1
            body = {
                "seq": self._seq,
                "ts": now_iso(),
                "run_id": self.run_id,
                "trace_id": self.run_id,
                "span_id": span_id or uuid.uuid4().hex[:16],
                "parent_span_id": parent_span_id,
                "event": event,
                "actor": actor,
                "stage": stage,
                "data": redact(data or {}),
                "dedupe_key": dedupe_key,
                "prev_hash": self._last_hash,
            }
            body["hash"] = hashlib.sha256((self._last_hash + canonical(body)).encode()).hexdigest()
            with self.path.open("a") as fh:
                fh.write(canonical(body) + "\n")
            self._last_hash = body["hash"]
            if dedupe_key:
                self._dedupe.add(dedupe_key)
        log.info("%s stage=%s actor=%s %s", event, stage, actor, canonical(body["data"])[:300])
        return body

    def events(self) -> list[dict[str, Any]]:
        return read_events(self.path)

    @staticmethod
    def verify(path: Path) -> VerifyResult:
        prev, count = GENESIS, 0
        if not path.exists():
            return VerifyResult(False, 0, "audit log not found")
        for n, line in enumerate(path.read_text().splitlines(), start=1):
            if not line.strip():
                continue
            rec = json.loads(line)
            claimed = rec.pop("hash", None)
            if rec.get("prev_hash") != prev:
                return VerifyResult(False, count, f"record {n}: chain broken (prev_hash mismatch)")
            if rec.get("seq") != count + 1:
                return VerifyResult(False, count, f"record {n}: sequence gap")
            actual = hashlib.sha256((prev + canonical(rec)).encode()).hexdigest()
            if actual != claimed:
                return VerifyResult(False, count, f"record {n}: content hash mismatch (tampered)")
            prev, count = claimed, count + 1
        return VerifyResult(True, count)
