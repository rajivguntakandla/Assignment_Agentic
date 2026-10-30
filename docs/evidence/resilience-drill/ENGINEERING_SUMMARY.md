# Engineering Summary - Bulk link creation API

- **Run:** `resilience-drill-20261030T164615`  
- **Scenario:** greenfield (`greenfield`)  
- **Outcome:** READY FOR MERGE (human-approved)  
- **LLM providers (in fallback order):** replay  
- **Execution path:** requirements -> planning -> design -> implementation -> documentation -> security_review -> verification -> sync -> release_approval -> release

## 1. Requirement understanding

**Intent:** Let partners create many short links in one call with independent per-item outcomes.

- **FR-1** POST /api/v1/links/bulk accepts between 1 and 100 link requests (url, optional customAlias, optional expiresAt).
  - AC: A request with 2 valid items returns HTTP 200 with created=2 and two results in request order.
  - AC: Each created item exposes the same fields as the single-create API (code, shortUrl, targetUrl, ...).
- **FR-2** Items are processed independently; an invalid or conflicting item does not prevent other items from being created.
  - AC: An unsafe URL is reported as status=error with error.status=400 while valid siblings are created.
  - AC: A duplicate custom alias within the batch is reported with error.status=409.
- **FR-3** Batches with zero items, more than 100 items, or no items field are rejected as a whole with HTTP 400.
  - AC: items: [] returns 400 with an RFC 9457 problem detail
  - AC: 101 items returns 400 and creates nothing

**Non-functional:** Same URL validation and alias rules as the single-create endpoint (single source of truth).; A bulk call consumes one rate-limit token; per-item work is bounded by the 100-item cap.

**Clarifications (human):** none needed

**Out of scope:** Asynchronous/background batch processing; Bulk deletion

## 2. Plan and rationale

DTOs are independent of each other and form the first wave; the controller depends on both; tests depend on the controller; docs close the loop. Reusing LinkService keeps validation in one place.

| Task | Kind | Depends on | Requirements | Files |
|---|---|---|---|---|
| T1 Bulk request DTO with 1..100 size constraint | code | - | FR-1, FR-3 | `src/main/java/io/agentic/shortener/web/dto/BulkCreateLinksRequest.java` |
| T2 Bulk response DTO with per-item result | code | - | FR-1, FR-2 | `src/main/java/io/agentic/shortener/web/dto/BulkCreateLinksResponse.java` |
| T3 BulkLinkController delegating each item to LinkService | code | T1, T2 | FR-1, FR-2, FR-3 | `src/main/java/io/agentic/shortener/web/BulkLinkController.java` |
| T4 MockMvc integration tests for bulk semantics | test | T3 | FR-1, FR-2, FR-3 | `src/test/java/io/agentic/shortener/web/BulkLinkApiIntegrationTest.java` |
| T5 Feature documentation and changelog | docs | T4 | - |  |

**Execution waves:** T1, T2 | T3 | T4 | T5

## 3. Architecture and codebase reasoning

New BulkLinkController at POST /api/v1/links/bulk that loops over the items and delegates each one to the existing LinkService.create, translating domain exceptions into per-item error results.

- **Risk:** medium (additive public API) - human approval not required
- **Impacted modules:** service, web
- **Affected endpoints:** DELETE /api/v1/links/{code}, GET /api/v1/links/{code}, GET /api/v1/links/{code}/stats, GET /{code:[A-Za-z0-9_-]{3,32}}, POST /api/v1/links
- **Tables:** click_events, links
- **Data flow:** LinkController -> AnalyticsService -> LinkService -> ClickEventRepository -> LinkRepository -> tables(click_events, links)
- **Data flow:** RedirectController -> LinkService -> ClickEventRepository -> LinkRepository -> tables(click_events, links)

- **D1** Reuse LinkService.create per item instead of a repository-level batch insert - *Validation, alias reservation and SSRF rules stay in one place; no duplicated business logic.* (rejected: Repository saveAll batch insert (faster, but duplicates validation and alias logic))
- **D2** Each item runs in its own transaction (partial success) - *Matches the requirement that items are independent; one bad URL must not roll back the others.* (rejected: All-or-nothing single transaction)
- **D3** Return HTTP 200 with per-item status instead of 207 Multi-Status - *207 is WebDAV-specific and poorly supported by client libraries; per-item status is explicit.* (rejected: 207 Multi-Status, 201 only when all succeed)
- **D4** Enforce 1..100 items with Bean Validation on the request DTO; do not cascade @Valid to items - *Batch-level shape errors fail fast with 400; item-level errors must be reported per item.* (rejected: Cascading @Valid (would fail the whole batch for one bad item))

## 4. Artifacts

- Patch: `artifacts/implementation-r1.patch` (+184 / -0); final release patch: `release/change.patch`
- Files: `src/main/java/io/agentic/shortener/web/BulkLinkController.java`, `src/main/java/io/agentic/shortener/web/dto/BulkCreateLinksRequest.java`, `src/main/java/io/agentic/shortener/web/dto/BulkCreateLinksResponse.java`, `src/test/java/io/agentic/shortener/web/BulkLinkApiIntegrationTest.java`
- Test methods (static count): 25 before -> 29 after
- Run directory: `orchestrator/runs/demo-20261030T164615/resilience-drill-20261030T164615`
- Audit log: `audit.jsonl` (hash-chained; `agentic audit verify <run>`); metrics: `metrics.json`
- Stage artifacts: `artifacts/`; build logs: `logs/`; snapshots: `snapshots/`

## 5. Traceability (requirement -> task -> code -> test)

| Requirement | Tasks | Files | Verified |
|---|---|---|---|
| FR-1 | T1, T2, T3, T4 | `src/main/java/io/agentic/shortener/web/BulkLinkController.java`, `src/main/java/io/agentic/shortener/web/dto/BulkCreateLinksRequest.java`, `src/main/java/io/agentic/shortener/web/dto/BulkCreateLinksResponse.java`, `src/test/java/io/agentic/shortener/web/BulkLinkApiIntegrationTest.java` | yes |
| FR-2 | T2, T3, T4 | `src/main/java/io/agentic/shortener/web/BulkLinkController.java`, `src/main/java/io/agentic/shortener/web/dto/BulkCreateLinksResponse.java`, `src/test/java/io/agentic/shortener/web/BulkLinkApiIntegrationTest.java` | yes |
| FR-3 | T1, T3, T4 | `src/main/java/io/agentic/shortener/web/BulkLinkController.java`, `src/main/java/io/agentic/shortener/web/dto/BulkCreateLinksRequest.java`, `src/test/java/io/agentic/shortener/web/BulkLinkApiIntegrationTest.java` | yes |

## 6. Validation

- **Build/test:** PASSED - 49 tests, 0 failures, 0 errors (`mvn -B -q test`, 9.35s)
- **Rework cycles:** 0
- **Security/compliance findings:** {'block': 0, 'require_approval': 0, 'warn': 1, 'total': 1}
  - [warn] SEC-200-new-endpoint `src/main/java/io/agentic/shortener/web/BulkLinkController.java`:41 New HTTP endpoint: confirm input validation and rate limiting
- **Gates:** design=completed, documentation=completed, implementation=completed, planning=completed, release=completed, release_approval=completed, requirements=completed, security_review=completed, sync=completed, verification=completed

## 7. Human oversight (every decision, in order)

- **release_approval** by `tech-lead@shortener-team` (pre-recorded scenario decision): approve - Reviewed patch and test evidence; additive endpoint, partial-success semantics documented.

## 8. Risks and trade-offs

- A bulk call performs up to 100 inserts for one rate-limit token (bounded amplification).
- Partial success requires clients to inspect every item result.
- A custom alias named 'bulk' would only be reachable via redirect, not via GET /api/v1/links/bulk metadata.

## 9. Decision lineage

| # | Stage | Actor | Decision | Rationale |
|---|---|---|---|---|
| requirements#1 | requirements | agent:requirements | Normalised requirement into 3 FR(s) | intent: Let partners create many short links in one call with independent per-item outcomes. |
| planning#1 | planning | agent:planning | Plan r1: 5 tasks in 4 waves | DTOs are independent of each other and form the first wave; the controller depends on both; tests depend on the controller; docs close the loop. Reusing LinkSer |
| design#1 | design | agent:design | D1: Reuse LinkService.create per item instead of a repository-level batch insert | Validation, alias reservation and SSRF rules stay in one place; no duplicated business logic. |
| design#2 | design | agent:design | D2: Each item runs in its own transaction (partial success) | Matches the requirement that items are independent; one bad URL must not roll back the others. |
| design#3 | design | agent:design | D3: Return HTTP 200 with per-item status instead of 207 Multi-Status | 207 is WebDAV-specific and poorly supported by client libraries; per-item status is explicit. |
| design#4 | design | agent:design | D4: Enforce 1..100 items with Bean Validation on the request DTO; do not cascade @Valid to items | Batch-level shape errors fail fast with 400; item-level errors must be reported per item. |
| design#5 | design | agent:design | Risk classified medium | additive public API |
| implementation#1 | implementation | agent:implementation | Change set r1: 4 file(s) | Add POST /api/v1/links/bulk with per-item results, reusing LinkService for every item. |
| documentation#1 | documentation | agent:documentation | Documented 3 requirement(s) in docs/features/bulk-link-creation-api.md | docs generated from spec + design, not from code comments |
| security_review#1 | security_review | agent:security_review | Security review: 0 blocking, 0 need approval, 1 warnings | policy-as-code scan of all added lines |
| verification#1 | verification | agent:verification | Build r1 green: 49 tests, 0 failures, 0 errors | mvn -B -q test |
| release_approval#1 | release_approval | human:tech-lead@shortener-team | release_approval: approve | Reviewed patch and test evidence; additive endpoint, partial-success semantics documented. |
| release#1 | release | agent:release | Release package with 6 file(s); not promoted | human-approved; patch is the reviewable outcome |

## 10. Reliability metrics

- **run_status:** completed
- **stage_executions:** 8
- **stage_success_rate:** 1.0
- **stage_failures:** 0
- **retries:** 3
- **retry_frequency:** 0.375
- **rollbacks:** 0
- **rollback_frequency:** 0.0
- **fallbacks:** 1
- **replans:** 0
- **safe_stops:** 1
- **approvals:** 2
- **policy_findings:** 1
- **mttr_s:** 0.066
- **recoveries:** 1
- **e2e_latency_s:** 11.239
- **human_wait_s:** 9.364
- **active_latency_s:** 1.875
- **llm_calls:** 8
- **llm_latency_p50_s:** 0.0
- **llm_latency_p95_s:** 0.001
- **retries_by_scope:** {'llm': 3}
- **llm_calls_by_provider:** {'replay': 4}
- **stage_latency_p50_s:** {'design': 0.487, 'documentation': 0.008, 'implementation': 0.072, 'planning': 1.159, 'release': 0.02, 'requirements': 0.003, 'security_review': 0.022, 'verification': 9.356}

## 11. Assumptions

- Idempotency-Key is not supported on bulk requests in this iteration.

## 12. Limitations

- Verification runs the project's own test suite in a sandbox copy; it proves behaviour the tests cover, not behaviour they omit. Human review of the patch remains mandatory (release checkpoint).
- Policy rules are pattern-based (added lines only); they catch known-bad constructs, not novel logic flaws.
- Impact analysis is static and type-level (imports/identifiers); reflection and configuration-driven wiring are not modelled.
- In replay mode the LLM responses are recorded fixtures: orchestration, governance, policy, execution and verification are real, but generation quality is that of the recording, not a live model.
