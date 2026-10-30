"""Sandboxed command execution for verification (allow-listed binaries, timeout, scrubbed environment)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET  # noqa: S405 - parsing our own build's surefire reports
from pathlib import Path

from .models import VerificationReport
from .resilience import PermanentError, TransientError

# "true"/"false" are side-effect-free binaries used to test the runner itself.
ALLOWED_BINARIES = {"mvn", "./mvnw", "mvnw", "true", "false"}
_SCRUB = ("ANTHROPIC", "AWS_", "GITHUB_TOKEN", "OPENAI", "SECRET", "PASSWORD", "TOKEN")


def scrubbed_env() -> dict[str, str]:
    """Generated code runs during tests; it must never see orchestrator credentials."""
    return {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in _SCRUB)}


class CommandRunner:
    def __init__(self, timeout_s: int):
        self.timeout_s = timeout_s

    def run(self, command: list[str], cwd: Path, log_file: Path) -> tuple[int, float, str]:
        if not command or command[0] not in ALLOWED_BINARIES:
            raise PermanentError(f"command not allow-listed: {command[:1]}")
        if shutil.which(command[0]) is None and not (cwd / command[0]).exists():
            raise PermanentError(f"required tool not installed: {command[0]}")
        started = time.monotonic()
        try:
            proc = subprocess.run(command, cwd=cwd, env=scrubbed_env(), capture_output=True, text=True,
                                  timeout=self.timeout_s, check=False)
        except subprocess.TimeoutExpired as exc:
            raise TransientError(f"verification timed out after {self.timeout_s}s") from exc
        output = (proc.stdout or "") + (proc.stderr or "")
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(output)
        return proc.returncode, time.monotonic() - started, output


def parse_surefire(workspace: Path) -> tuple[int, int, int, int, list[str]]:
    tests = failures = errors = skipped = 0
    failed: list[str] = []
    for xml in sorted((workspace / "target" / "surefire-reports").glob("TEST-*.xml")):
        root = ET.parse(xml).getroot()  # noqa: S314
        tests += int(root.get("tests", 0))
        failures += int(root.get("failures", 0))
        errors += int(root.get("errors", 0))
        skipped += int(root.get("skipped", 0))
        for case in root.iter("testcase"):
            problem = case.find("failure") if case.find("failure") is not None else case.find("error")
            if problem is not None:
                msg = (problem.get("message") or problem.text or "").strip().splitlines()
                failed.append(f"{case.get('classname')}.{case.get('name')}: {msg[0][:300] if msg else ''}")
    return tests, failures, errors, skipped, failed


def verify(runner: CommandRunner, command: list[str], workspace: Path, log_file: Path) -> VerificationReport:
    reports = workspace / "target" / "surefire-reports"
    if reports.exists():
        shutil.rmtree(reports)
    code, duration, output = runner.run(command, workspace, log_file)
    tests, failures, errors, skipped, failed = parse_surefire(workspace)
    if code != 0 and not failed:
        # Compilation errors produce no surefire reports; surface the compiler output instead.
        failed = [line.strip() for line in output.splitlines() if "[ERROR]" in line][:15]
    return VerificationReport(
        passed=code == 0 and failures == 0 and errors == 0 and tests > 0,
        command=command, exit_code=code, duration_s=round(duration, 2), tests=tests, failures=failures,
        errors=errors, skipped=skipped, failed_tests=failed, log_tail="\n".join(output.splitlines()[-40:]),
    )
