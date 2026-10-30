"""Safe-stop controls and autonomy budgets."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from .resilience import BudgetExceeded
from .settings import Budget


class SafeStopController:
    """A run stops at the next stage boundary when its kill-switch file exists (``agentic stop <run>``).

    Stopping is a durable *pause*, not a crash: the graph interrupts, state is checkpointed, and an operator
    either resumes or aborts (abort rolls the workspace back to its baseline).
    """

    def __init__(self, run_dir: Path):
        self.kill_switch = run_dir / "STOP"

    def requested(self) -> bool:
        return self.kill_switch.exists()

    def request(self, reason: str) -> None:
        self.kill_switch.write_text(reason)

    def reason(self) -> str:
        return self.kill_switch.read_text().strip() if self.kill_switch.exists() else ""

    def clear(self) -> None:
        self.kill_switch.unlink(missing_ok=True)


class BudgetGuard:
    """Tracks consumption against the run budget; exceeding it is a terminal safe stop."""

    def __init__(self, budget: Budget, started_monotonic: float | None = None):
        self.budget = budget
        self.started = started_monotonic or time.monotonic()
        self.llm_calls = 0
        self._lock = threading.Lock()

    def charge_llm_call(self) -> None:
        with self._lock:
            self.llm_calls += 1
            if self.llm_calls > self.budget.max_llm_calls:
                raise BudgetExceeded(f"LLM call budget exhausted ({self.budget.max_llm_calls})")

    def check_wall_clock(self) -> None:
        elapsed = time.monotonic() - self.started
        if elapsed > self.budget.max_wall_clock_s:
            raise BudgetExceeded(f"wall-clock budget exhausted ({elapsed:.0f}s > {self.budget.max_wall_clock_s}s)")
