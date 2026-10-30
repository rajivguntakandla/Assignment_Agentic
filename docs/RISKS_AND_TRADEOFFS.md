# Risks, failure scenarios, guardrails and trade-offs

## Failure scenarios and the guardrail that handles each

| # | Failure scenario | Guardrail | Proven by |
|---|---|---|---|
| 1 | LLM transient error / rate limit / 5xx | Bounded retries with exponential backoff + jitter; SDK retries disabled so the policy is single and audited | `test_transient_errors_are_retried_with_backoff`, resilience drill |
| 2 | LLM returns malformed or schema-invalid output | Pydantic validation + semantic validators; corrective feedback; bounded | `test_invalid_output_gets_corrective_feedback` |
| 3 | Claude refuses (safety classifier) | Server-side `fallbacks: "default"`; then provider chain; refusal treated as permanent for that provider | `test_refusal_is_permanent` |
| 4 | Provider outage | Fallback to the next provider, then a deterministic agent fallback where safe | `test_llm_faults_are_retried_and_fall_back` |
| 5 | Model under-states risk | Risk classification is deterministic (policy paths + static analysis); LLM output ignored for it | greenfield/brownfield/ambiguous risk assertions |
| 6 | Model hides ambiguity | Deterministic ambiguity analyser can only add ambiguity | `test_ambiguous_clarification_and_design_rejection_loops` |
| 7 | Generated code is dangerous (exec, deserialisation, SQL concat, TLS bypass, secrets, PII logging) | Policy-as-code on added lines, block -> rollback -> feedback -> bounded -> halt | `test_policy_violation_halts_and_restores_baseline`, `test_content_rules_scan_only_added_lines` |
| 8 | Generated code edits outside its remit (CI config, build wrapper, applied migrations) | Path allowlist, protected paths, immutable migrations, scope-creep check vs approved design | `test_path_controls`, `test_change_control_on_diff` |
| 9 | Generated code "passes" by deleting tests | Static test-count regression check; zero-test runs are not green | `test_change_control_on_diff`, `test_verify_requires_tests_to_have_run` |
| 10 | Generated code is wrong | Real build + test suite; rework loop with rollback; bounded by `max_rework_cycles` | brownfield scenario (real off-by-one caught), `test_rework_budget_is_bounded` |
| 11 | Agent self-approves | Separation of duties: non-human approver identities rejected; fail closed | `test_separation_of_duties_rejects_non_human_approver` |
| 12 | Human approves without reading a sensitive finding | `require_approval` findings must be explicitly acknowledged | ambiguous scenario |
| 13 | Runaway agent (cost/time) | Budgets on LLM calls, wall clock, rework, design revisions, clarification rounds -> safe halt | `test_budget_exhaustion_is_a_safe_halt` |
| 14 | Operator needs to stop everything now | Kill switch -> durable pause at next stage boundary in every branch; continue or abort (abort = rollback) | `test_safe_stop_*`, `test_operator_stop_request_pauses_running_graph` |
| 15 | Orchestrator process crashes | SQLite checkpoint after every superstep; `resume` continues without redoing completed stages | `test_crash_is_recovered_from_checkpoint` |
| 16 | Audit trail tampered with | SHA-256 hash chain + sequence numbers; `agentic audit verify` | `test_chain_verifies_and_detects_tampering`, `test_cli_stop_and_tampered_audit` |
| 17 | Secrets leak to logs or to generated code at test time | Audit redaction; build environment scrubbed of `ANTHROPIC*`, `AWS_*`, tokens, passwords | `test_secrets_are_redacted`, `test_environment_is_scrubbed` |
| 18 | Path traversal from model output | Relative-path validation + resolved-path containment check | `test_paths_cannot_escape_the_workspace` |
| 19 | Parallel branches race on shared files | Branches share no mutable state; docs written outside the workspace; reducers for state | architecture; repeated green runs |
| 20 | Stale context after upstream change | Output fingerprints -> downstream invalidation; entry gates refuse stale inputs | `test_entry_gate_rejects_stale_inputs`, brownfield/ambiguous replans |
| 21 | Reusing a run id merges old state | Refused; orphan checkpoint threads are deleted on start | `test_run_ids_cannot_be_reused` |

### Service-level risks (url-shortener)

| Risk | Mitigation |
|---|---|
| Open redirect / phishing / SSRF via stored URLs | Scheme allowlist, no userinfo, private/loopback/link-local/CGNAT literals and internal TLDs rejected, self-links rejected, optional domain denylist (ambiguous scenario) |
| Code enumeration | 62^7 random codes from `SecureRandom` |
| Abuse of the write API | Per-client token bucket, bounded key map; 429 + `Retry-After` |
| Duplicate creation on client retries | `Idempotency-Key` with request-equivalence check |
| Lost click counts under concurrency | Atomic SQL increment |
| Analytics failure taking down redirects | Isolated, logged and counted; redirect still served |
| Privacy | Only salted hashes of visitor IP + UA stored; no raw IPs in DB or logs |

## Trade-offs

| Choice | Benefit | Cost |
|---|---|---|
| Real build as oracle | Strong evidence | Verification takes ~10 s per round; scales with the test suite |
| Pattern-based policy | Fast, explainable, configurable | Misses novel logic flaws; human review stays mandatory |
| Static, type-level impact analysis | Deterministic, no toolchain dependency | Misses reflection/config-driven wiring; over-approximates (conservative) |
| Replay provider for offline mode | Reproducible CI/demos without keys | Generation quality is that of the recording; live quality needs `--llm claude` |
| Design approval only for high risk | Keeps humans on what matters; less approval fatigue | A mis-configured risk policy could under-gate (mitigated: release approval always applies) |
| Fail closed on invalid approvals | Safety | An honest mistake by an operator halts the run (restart needed) |
| In-memory rate limiter | Simple, zero dependencies | Per instance; needs Redis when scaled out |
| Synchronous click recording | Simple, consistent | Adds a DB write to the redirect path; at scale move to an async queue |
| 302 instead of 301 | Accurate analytics, enforceable expiry | No browser caching of redirects |
| Fixed stage topology with dynamic loops | Auditable, testable control flow | New stage types require code, not configuration |

## Assumptions

- The target project builds with Maven and has a meaningful test suite (true for `url-shortener/`).
- Reviewers named in scenario files stand in for real people in unattended demos; interactive and manual modes
  capture real identities.
- A single orchestrator process works on a run at a time (SQLite checkpointer).

## Limitations

- The scenario evidence uses recorded responses (reproducible, keyless). The live path was exercised once end to end
  on an ad-hoc requirement (`evidence/live-claude/`, Claude as the only provider); a single live run is a smoke test
  of generation quality, not a statistical evaluation.
- Policy rules scan Java and YAML text, not an AST.
- No hosted UI; observability is the audit log, metrics JSON/Prometheus text and markdown summaries.
- Promotion writes changed files into the target project; it does not create commits or PRs. That is intentional:
  humans own merges.
