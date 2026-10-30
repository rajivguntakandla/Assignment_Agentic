# Testing approach

The testing approach follows from one principle: **evidence over assertion**. The orchestrator never takes a
model's word that code works. It runs the project's real build, and its own behaviour is pinned down by tests at
three levels.

## Pyramid

| Level | Where | What it proves | Runtime |
|---|---|---|---|
| Java unit | `url-shortener/src/test/.../service`, `ratelimit` | URL safety rules (24 cases incl. SSRF/phishing), code generation, token bucket incl. refill and LRU eviction | < 1 s |
| Java integration | `LinkApiIntegrationTest`, `RateLimitIntegrationTest` | Full HTTP contract via MockMvc + in-memory DB: create, alias, idempotency, validation, expiry with a controllable `Clock`, deactivation, analytics aggregation, 429 + `Retry-After`, health, OpenAPI | ~6 s |
| Orchestrator unit | `orchestrator/tests/test_{audit,policy,dag_gates,ambiguity,workspace_codebase,llm_router,metrics,claude_model,cli_executor}.py` | Each governance mechanism in isolation: hash-chain tamper detection, every policy rule, gates, wave computation, cycle detection, impact analysis on the real service, retries/backoff/feedback/fallback/budget, metric formulas, Claude request shape and error classification | ~2 s |
| Orchestration (graph) | `orchestrator/tests/test_orchestration.py` | The real LangGraph graph with the real policy engine, workspace, audit and checkpointer, with the build replaced by a scripted fake. Covers every path: parallel fan-out/fan-in, rework + rollback, clarification and design-rejection loops, policy halt with baseline restore, manual pause/resume, release change-requests, separation of duties, safe stop (continue/abort/operator-requested), crash + resume, LLM faults, budget exhaustion, bounded rework, run-id reuse | ~5 s |
| Live LLM | `agentic run --llm claude` (`docs/evidence/live-claude/`) | A real model's spec, design, code and tests pass the same gates, policy and real build | ~12 min |
| End-to-end | `orchestrator/tests/test_integration_maven.py` (`-m integration`) and `agentic demo` | Each scenario's generated change set compiles and passes the **real** Maven test suite; brownfield needs exactly one rework | ~40 s / ~70 s |

```bash
make test               # Java verify + orchestrator lint/unit/orchestration
make test-integration   # scenarios against the real Maven build
make demo               # all scenarios + drills, regenerates docs/evidence
```

Current results: Java **45/45**, orchestrator **70/70** (fast suite) + **3/3** integration, `ruff` clean,
93% line coverage of `agentic_sdlc` in the fast suite (the demo runner is exercised by `make demo` and CI).

## How generated code is validated

1. **Pre-apply**: change set schema, plan traceability (every code/test task has a change), path policy.
2. **Apply** in the sandbox: all-or-nothing, each edit's search text must match exactly once.
3. **Post-apply**: diff policy on added lines (security, compliance, change control), test-count regression check,
   tests-required rule, scope check against the approved design.
4. **Verification**: real `mvn test` in the sandbox with a scrubbed environment; Surefire reports parsed. A green
   exit code with zero tests is *not* a pass.
5. **Independent security review** of the full diff in parallel.
6. **Human release approval**, which must acknowledge every `require_approval` finding.

## Determinism

Offline runs use recorded LLM responses (`scenarios/*.yaml`), so CI is reproducible and needs no keys or network.
Fault injection (`--fault llm_transient:<agent>=N`, `invalid_output:<agent>`, `llm_down:<agent>`,
`safe_stop:<stage>`, `crash:<stage>`) makes failure paths testable. Consumed faults are persisted per run, so a
resumed run does not re-fire them.

## CI

- `.github/workflows/java-ci.yml`: `mvn -B verify` on JDK 21, Surefire reports uploaded.
- `.github/workflows/orchestrator-ci.yml`: ruff + pytest with coverage, then integration tests and the full demo
  against the real build; every audit chain is verified and the evidence pack uploaded as an artifact.
