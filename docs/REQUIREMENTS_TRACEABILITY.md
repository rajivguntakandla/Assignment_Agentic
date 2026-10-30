# Requirements traceability

Each assessment item maps to the code that implements it and to **executed** evidence: a test that asserts the
behaviour, and/or an artifact in [`evidence/`](evidence/README.md) produced by a real run. Paths are relative to
the repository root; `O/` = `orchestrator/agentic_sdlc/`, `T/` = `orchestrator/tests/`, `E/` = `docs/evidence/`.

## A. Assessment checklist (25 items)

| # | Requirement | Implementation | Executed evidence |
|---|---|---|---|
| 1 | Requirement understanding and ambiguity detection | `O/agents/requirements.py`, `O/ambiguity.py` (deterministic, add-only guardrail), clarification checkpoint in `O/engine.py` | `T/test_ambiguity.py`; `test_ambiguous_clarification_and_design_rejection_loops`; `E/ambiguous/ENGINEERING_SUMMARY.md` §1 |
| 2 | Task decomposition with dependencies | `O/agents/planning.py` (validated task DAG, FR coverage), `O/gates.py::waves` | `test_waves_group_independent_tasks`, `test_waves_detect_cycles`, `test_planning_exit_gate_requires_coverage_and_tests`; summaries §2 (waves) |
| 3 | Brownfield codebase reasoning | `O/codebase.py` (layers, stereotypes, endpoints, tables, reverse-dependency impact, data flows), `O/agents/design.py` | `test_codebase_index_on_real_service`; `E/brownfield/ENGINEERING_SUMMARY.md` §3 |
| 4 | Explicit dependency graph | `O/dag.py` (`STAGES.consumes`, `CONTROL_EDGES`, topological order, control-flow validation, mermaid) | `test_dag_is_acyclic_and_control_flow_consistent`, `test_downstream_invalidation_set`; `agentic graph` |
| 5 | Sequential orchestration | `O/engine.py::_build` (requirements -> planning -> design -> implementation) | every scenario path in `E/README.md` |
| 6 | Parallel orchestration | Fan-out `implementation -> {verification, security_review, documentation}` (`_after_implementation` returns a list) | `test_greenfield_runs_parallel_validation_and_skips_design_approval` |
| 7 | Synchronisation and stage gates | `sync` barrier (`add_edge([...], "sync")`), `O/gates.py` entry/exit gates audited as `gate_evaluated` | `T/test_dag_gates.py`; `gate_evaluated` events in `E/*/audit.jsonl` |
| 8 | Persistent state and resumability | LangGraph `SqliteSaver` (`O/runner.py`), `agentic resume`, persisted fault consumption | `test_crash_is_recovered_from_checkpoint`, `test_manual_approvals_pause_durably_and_resume`; resilience drill |
| 9 | Cross-stage context | Typed artifacts (`O/models.py`), least-privilege inputs per `consumes`, explicit feedback channels | `test_brownfield_failure_triggers_rollback_rework_and_recovers` (failing tests reach round 2) |
| 10 | Decision lineage and provenance | `O/engine.py::_decisions` (actor, rationale, input fingerprints, `supersedes`) | `test_ambiguous_...` (revision 2 supersedes 1); summaries §9 |
| 11 | Human approval checkpoints | `interrupt()` at clarification / design_approval (high risk) / release_approval; `O/approvals.py` | `test_manual_approvals_pause_durably_and_resume`, `test_release_request_changes_loops_back_to_implementation` |
| 12 | Bounded retries | `O/resilience.py::retry_call` (LLM + stage), `max_rework_cycles`, `max_design_revisions`, `max_clarification_rounds` | `test_transient_errors_are_retried_with_backoff`, `test_rework_budget_is_bounded` |
| 13 | Fallback mechanisms | Claude server-side `fallbacks`, provider chain in `O/llm.py::LLMRouter`, deterministic agent fallbacks (`O/agents/base.py`) | `test_falls_back_to_next_provider`, `test_llm_faults_are_retried_and_fall_back`, `test_adhoc_requirement_without_llm_uses_safe_fallbacks_then_halts` |
| 14 | Rollback | `O/workspace.py` snapshots; rollback on policy block, failed validation, change request, halt | `test_snapshot_and_restore`, `test_policy_violation_halts_and_restores_baseline`; `rollback` events in `E/brownfield/audit.jsonl` |
| 15 | Safe-stop controls | `O/safety.py` kill switch + budgets; durable pause via `interrupt`, continue/abort | `test_safe_stop_pauses_and_operator_can_continue_or_abort`, `test_operator_stop_request_pauses_running_graph`, `test_budget_exhaustion_is_a_safe_halt` |
| 16 | Security and compliance guardrails | `O/policy.py` + `O/config/policies.yaml` (security, compliance, change control), separation of duties, env scrubbing, redaction | `T/test_policy.py`, `test_separation_of_duties_...`, `test_environment_is_scrubbed`; `E/policy-violation/` |
| 17 | Audit-grade observability | `O/audit.py`: hash chain, sequence numbers, trace/span ids, redaction, idempotent emission; `agentic audit verify/show` | `T/test_audit.py` (tamper, delete, reorder), `test_cli_stop_and_tampered_audit`; `E/*/audit.jsonl` (all verified valid) |
| 18 | Reliability metrics | `O/metrics.py`: success rate, retry/rollback/fallback frequency, MTTR (stage failures and crashes), e2e/active latency, Prometheus | `T/test_metrics.py`; `E/metrics.json`, `E/metrics.prom` |
| 19 | Dynamic re-planning | Output fingerprints -> downstream invalidation (`O/engine.py::_replan`), rework re-plan, stale-input entry gate | `test_entry_gate_rejects_stale_inputs`; `replan` events in `E/brownfield` and `E/ambiguous` |
| 20 | AI-generated engineering outputs | Requirements, plan, design, change sets, docs from the LLM chain (`ClaudeChatModel` / replay) | `E/live-claude/` (generated live by Claude, 67/67 tests green); `E/*/change.patch`; `test_request_shape_and_text_extraction` |
| 21 | Actual automated testing | `O/agents/verification.py` + `O/executor.py` run the real Maven suite on generated code | `T/test_integration_maven.py`; `E/brownfield/verification-r1.log` (2 real failures) and `-r2.log` (green) |
| 22 | Generated documentation | `O/agents/documentation.py` (gated: references every FR id) merged into the patch by `release` | `docs/features/*.md` in every `E/*/change.patch` |
| 23 | Risks and trade-offs | Per-run design risks + findings in summaries; `docs/RISKS_AND_TRADEOFFS.md` | summaries §8 |
| 24 | Controlled agent autonomy | Autonomy boundaries: sandbox only, allow-listed commands, policy, budgets, deterministic risk gating, no code fallback, human release gate, promotion needs explicit authorisation | `test_policy_violation_...`, `test_budget_...`, `test_separation_of_duties_...` |
| 25 | Final engineering summary | `O/report.py` per run (plan/rationale, artifacts, traceability, validation, approvals, risks, lineage, metrics, assumptions, limitations); `docs/ENGINEERING_SUMMARY.md` | `E/*/ENGINEERING_SUMMARY.md` |

## B. Verification checklist

| Item | Where |
|---|---|
| Java Spring Boot application | `url-shortener/` (Spring Boot 3.5.7, Java 21) - 45 tests green |
| LangGraph and LangChain integration | `O/engine.py` (`StateGraph`, `interrupt`, `Command`, `SqliteSaver`), `O/llm.py` (LangChain `BaseChatModel`, `ChatPromptTemplate`) |
| LLM integration code | `O/llm.py::ClaudeChatModel` - official Anthropic SDK, `claude-opus-5-5`, streaming, effort, server-side refusal fallbacks, typed error classification; exercised live in `E/live-claude/` |
| Agentic workflow automation | `agentic run / resume / stop / status / audit / metrics / graph / demo` (`O/cli.py`) |
| Governance and recovery tests | `T/test_orchestration.py`, `T/test_policy.py`, `T/test_audit.py`, `T/test_llm_router.py` |
| Updated technical documentation | `README.md`, `docs/*.md`, `url-shortener/README.md` |
| Java CI | `.github/workflows/java-ci.yml` |
| LangGraph Python CI | `.github/workflows/orchestrator-ci.yml` (lint, unit, integration, full demo, audit verification, evidence artifact) |

## C. Assignment PDF

| Section | Covered by |
|---|---|
| 1 Objective: requirement -> reviewable outcome, controlled autonomy | Patch + engineering summary + human release gate per run |
| 2 Scenario: URL shortener with core APIs, analytics, reliability | `url-shortener/` (create/redirect/metadata/stats/deactivate; daily series, unique visitors, referrers; idempotency, rate limiting, atomic counters, failure isolation, health, problem details) |
| 3 Scope: greenfield, brownfield, tests/docs, well-defined + ambiguous | `docs/SCENARIOS.md` §1-3 |
| 4.1-4.8 Core requirements | Section A rows 1-25 |
| 5 Deliverables: prototype, architecture, 3 scenarios, setup, testing/limitations/trade-offs | `make demo`; `docs/ARCHITECTURE.md`; `docs/SCENARIOS.md`; `README.md`; `docs/TESTING.md`; `docs/RISKS_AND_TRADEOFFS.md` |
| 7 Expectation: agents within autonomy boundaries, humans own approvals and final quality | Section A rows 11, 15, 16, 24 |
