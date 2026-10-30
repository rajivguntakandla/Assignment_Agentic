from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest

from agentic_sdlc.models import VerificationReport
from agentic_sdlc.settings import Budget, Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(home=tmp_path / "runs", llm_mode="offline", llm_backoff_base_s=0.0, budget=Budget())


class FakeBuild:
    """Replaces the Maven build in fast tests. Queue results: True = green, False = one failing test."""

    def __init__(self) -> None:
        self.results: deque[bool] = deque()
        self.calls = 0

    def __call__(self, runner, command, workspace, log_file) -> VerificationReport:  # noqa: ANN001
        self.calls += 1
        ok = self.results.popleft() if self.results else True
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("fake build\n")
        return VerificationReport(passed=ok, command=command, exit_code=0 if ok else 1, duration_s=0.01, tests=50,
                                  failures=0 if ok else 1,
                                  failed_tests=[] if ok else ["FooTest.bar: expected 410 but was 302"])


@pytest.fixture
def fake_build(monkeypatch: pytest.MonkeyPatch) -> FakeBuild:
    fake = FakeBuild()
    monkeypatch.setattr("agentic_sdlc.agents.verification.verify", fake)
    return fake
