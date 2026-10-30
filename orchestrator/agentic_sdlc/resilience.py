"""Bounded retries with exponential backoff, error classification and fault injection."""

from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


class TransientError(Exception):
    """Worth retrying: timeouts, rate limits, 5xx, flaky I/O."""


class PermanentError(Exception):
    """Retrying the same input will not help: bad request, auth, policy violation."""


class OutputValidationError(Exception):
    """The agent produced output that failed schema or semantic validation; retry with feedback."""


class SafeStopRequested(Exception):  # noqa: N818 - control-flow signal, not an error
    pass


class BudgetExceeded(Exception):  # noqa: N818
    pass


def retry_call(
    fn: Callable[[int], T],
    *,
    max_retries: int,
    backoff_base_s: float,
    retry_on: tuple[type[BaseException], ...] = (TransientError,),
    on_retry: Callable[[int, BaseException, float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn(attempt)`` up to ``max_retries + 1`` times. Only ``retry_on`` errors are retried."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn(attempt)
        except retry_on as exc:
            if attempt > max_retries:
                raise
            delay = backoff_base_s * (2 ** (attempt - 1)) * (0.5 + random.random() / 2)
            if on_retry:
                on_retry(attempt, exc, delay)
            sleep(delay)


class FaultInjector:
    """Deterministic fault injection used by tests and the resilience demo.

    Keys: ``llm_transient:<agent>`` (N transient LLM errors), ``llm_down:<agent>`` (primary provider fails
    permanently N times), ``crash:<stage>`` (unhandled crash N times), ``safe_stop:<stage>`` (request a safe stop
    before the stage), ``invalid_output:<agent>`` (agent returns malformed output N times).
    """

    def __init__(self, faults: dict[str, int] | None = None, state_file: Path | None = None):
        self._state_file = state_file
        self._remaining = dict(faults or {})
        if state_file and state_file.exists():
            # Consumption survives process restarts: a fault that already fired must not re-fire on resume.
            self._remaining = json.loads(state_file.read_text())
        self._lock = threading.Lock()

    def take(self, key: str) -> bool:
        with self._lock:
            n = self._remaining.get(key, 0)
            if n > 0:
                self._remaining[key] = n - 1
                if self._state_file:
                    self._state_file.write_text(json.dumps(self._remaining))
                return True
            return False
