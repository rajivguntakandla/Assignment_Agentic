# Final engineering summary

## Plan and rationale

The assignment asks for two things that are easy to conflate: a **URL shortener** and an **agentic system that
delivers changes to it under governance**. I built them as separate deliverables with a clean seam: the
orchestrator treats the service as an external target project, works on a sandbox copy, and proves its outputs
with the service's own test suite.

| Phase | Outcome |
|---|---|
| 1. Service baseline | Spring Boot service with link CRUD, redirect, analytics, idempotency, rate limiting and URL safety; 45 tests |
| 2. Orchestration core | Explicit DAG, LangGraph engine, gates, checkpoints, durable state |
| 3. Governance | Policy-as-code, sandbox + rollback, budgets, kill switch, separation of duties, hash-chained audit, derived metrics |
| 4. Agents and LLM | Claude via the official SDK as a LangChain model; replay provider for reproducibility; deterministic guardrails around every model output |
| 5. Scenarios and drills | Greenfield, brownfield, ambiguous, policy violation, resilience; all executed against the real build |
| 6. Evidence and docs | Evidence pack, architecture, scenarios, testing, risks, traceability, CI |

The design principle throughout: **the model proposes, deterministic code disposes**. Ambiguity detection, impact
analysis, risk classification, policy, gates and verification are code. The LLM supplies interpretation, design and
implementation, and every output is schema-validated, policy-checked and tested before a human sees it.

## Artifacts

| Artifact | Location |
|---|---|
| Service + tests | `url-shortener/` |
| Orchestrator + tests | `orchestrator/` (≈3.6k lines of code, 70 fast tests + 3 Maven-backed integration tests) |
| Scenarios (requirements, recorded human decisions, recorded LLM responses) | `orchestrator/scenarios/*.yaml` |
| Policies | `orchestrator/agentic_sdlc/config/policies.yaml` |
| Evidence of execution (patches, summaries, audit logs, metrics, build logs) | `docs/evidence/` |
| CI | `.github/workflows/` |
| Documentation | `README.md`, `docs/` |

## Results

| Scenario | Outcome | Notes |
|---|---|---|
| Greenfield (bulk API) | completed, 49 tests green | medium risk: no design gate; parallel validation |
| Brownfield (click limits) | completed after 1 rework, 50 tests green | high risk -> design approval; real off-by-one caught by tests, rolled back, fixed |
| Ambiguous ("safer links") | completed, 55 tests green | clarification checkpoint -> re-plan; design rejected once by a human, revised, approved; config change acknowledged |
| Policy violation | halted (expected) | `ProcessBuilder` blocked twice, workspace restored to baseline |
| Resilience drill | completed | transient errors, invalid output, provider outage, safe stop, process crash: all recovered |
| **Live Claude** (ad-hoc: list recent links) | completed, 67 tests green | every artifact generated live by `claude-opus-5-5`, no recordings; Claude raised 2 clarification questions; design rev 1 rejected in review (wrong visibility rule, error would map to 500), rev 2 approved; 22 new tests; 0 fallbacks |

Aggregate: stage success rate 0.95, retry frequency 0.12, rollback frequency 0.10, MTTR ≈ 4.6 s, p50 end-to-end
≈ 9 s (`docs/evidence/metrics.json`).

## Risks, trade-offs and validation

See [RISKS_AND_TRADEOFFS.md](RISKS_AND_TRADEOFFS.md) (21 failure scenarios, each mapped to a guardrail and to the
test that proves it) and [TESTING.md](TESTING.md). The most important trade-offs:

- Real-build verification is slower than LLM judgement, but it is evidence.
- Pattern-based policy is explainable and fast, but cannot find novel logic flaws, so the human release gate is
  mandatory, never optional.
- Design approval only for high-risk changes reduces approval fatigue; the release gate covers the rest.
- Replay mode makes CI deterministic but does not measure live generation quality.

## Assumptions

- Maven project with a meaningful test suite; JDK 21+ and Python 3.11+ available.
- Named reviewers in scenario files stand in for people during unattended demos; manual/interactive modes record
  real identities.
- Single orchestrator process per run (SQLite checkpointer); a shared Postgres checkpointer would be the next step.

## Limitations and next steps

| Limitation | Next step |
|---|---|
| Scenario evidence uses recorded LLM responses; one live run so far | Add an eval set of live runs to measure generation quality over many requirements |
| Text-based policy rules | AST-based rules (e.g. Semgrep) as an additional policy provider |
| Type-level impact analysis | Call-graph analysis via a Java parser; include tests in the impact set |
| Fixed stage topology | Declarative pipeline definitions per change type |
| Local SQLite checkpoints, CLI-only approvals | Postgres checkpointer; approvals through PR reviews or chat with signed identities |
| Single-node rate limiting and synchronous analytics in the service | Redis-backed limiter; async click pipeline |
