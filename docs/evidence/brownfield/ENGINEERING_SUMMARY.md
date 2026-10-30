# Engineering Summary - Click limits for short links

- **Run:** `brownfield-20261030T164615`  
- **Scenario:** brownfield (`brownfield`)  
- **Outcome:** READY FOR MERGE (human-approved)  
- **LLM providers (in fallback order):** replay  
- **Execution path:** requirements -> planning -> design -> design_approval -> implementation -> documentation -> security_review -> verification -> sync -> implementation -> documentation -> security_review -> verification -> sync -> release_approval -> release

## 1. Requirement understanding

**Intent:** Allow single-use or limited-use links that stop redirecting after a configured number of clicks.

- **FR-1** POST /api/v1/links accepts an optional maxClicks between 1 and 1,000,000.
  - AC: maxClicks=2 is stored and echoed in the 201 response together with remainingClicks=2.
  - AC: maxClicks=0, negative values and values above 1,000,000 are rejected with HTTP 400.
- **FR-2** A link with maxClicks=N redirects exactly N times; every later redirect returns HTTP 410 Gone.
  - AC: With maxClicks=2 the first two GET /{code} return 302 and the third returns 410.
  - AC: Refused redirects are not counted as clicks.
- **FR-3** Link metadata exposes maxClicks and remainingClicks.
  - AC: After two clicks on a maxClicks=2 link, GET /api/v1/links/{code} returns clickCount=2 and remainingClicks=0.
- **FR-4** Links without maxClicks behave exactly as before (unlimited).
  - AC: A link without maxClicks keeps redirecting (302) after many clicks and reports maxClicks=null.
  - AC: Idempotent replays must match maxClicks; a different maxClicks under the same Idempotency-Key returns 409.

**Non-functional:** The limit must hold under concurrent redirects (no overshoot).; Backward-compatible schema migration; existing rows remain valid.

**Clarifications (human):** none needed

**Out of scope:** Changing the limit of an existing link (PATCH)

## 2. Plan and rationale

Schema first (immutable V1, new V2), then the entity, then API/command types that depend on the entity accessors, then the service logic that ties them together, then tests. Strictly sequential because each layer compiles against the previous one.

| Task | Kind | Depends on | Requirements | Files |
|---|---|---|---|---|
| T1 Flyway V2 migration adding nullable max_clicks with check constraint | migration | - | FR-1, FR-4 | `src/main/resources/db/migration/V2__add_link_max_clicks.sql` |
| T2 Link entity field | code | T1 | FR-1, FR-3 | `src/main/java/io/agentic/shortener/domain/Link.java` |
| T3 Race-free conditional increment in LinkRepository | code | T2 | FR-2 | `src/main/java/io/agentic/shortener/repository/LinkRepository.java` |
| T4 Request/command/response API fields | code | T2 | FR-1, FR-3 | `src/main/java/io/agentic/shortener/web/dto/CreateLinkRequest.java`, `src/main/java/io/agentic/shortener/service/CreateLinkCommand.java`, `src/main/java/io/agentic/shortener/web/dto/LinkResponse.java`, `src/main/java/io/agentic/shortener/web/LinkController.java` |
| T5 Enforce limit in LinkService redirect path; idempotency comparison | code | T3, T4 | FR-2, FR-4 | `src/main/java/io/agentic/shortener/service/LinkService.java` |
| T6 Integration tests for limits | test | T5 | FR-1, FR-2, FR-3, FR-4 | `src/test/java/io/agentic/shortener/web/MaxClicksIntegrationTest.java` |
| T7 Feature documentation and changelog | docs | T6 | - |  |

**Execution waves:** T1 | T2 | T3, T4 | T5 | T6 | T7

## 3. Architecture and codebase reasoning

Add a nullable max_clicks column (new Flyway V2 migration) and enforce the limit in the redirect path with a single conditional UPDATE (increment only while click_count < max_clicks). Unlimited links keep the existing code path unchanged. API gains maxClicks (request) and maxClicks/remainingClicks (response).

- **Risk:** high (schema/data-model change; existing public API modified) - human approval required
- **Impacted modules:** domain, repository, service, web
- **Affected endpoints:** DELETE /api/v1/links/{code}, GET /api/v1/links/{code}, GET /api/v1/links/{code}/stats, GET /{code:[A-Za-z0-9_-]{3,32}}, POST /api/v1/links
- **Tables:** click_events, links
- **Data flow:** LinkController -> AnalyticsService -> LinkService -> ClickEventRepository -> LinkRepository -> tables(click_events, links)
- **Data flow:** RedirectController -> LinkService -> ClickEventRepository -> LinkRepository -> tables(click_events, links)

- **D1** New migration V2 adds nullable max_clicks INTEGER with CHECK (max_clicks > 0) - *Applied migrations are immutable; NULL means unlimited, so every existing row stays valid (FR-4).* (rejected: Edit V1 (breaks Flyway checksums in deployed environments), NOT NULL with sentinel value)
- **D2** Enforce the limit with a conditional UPDATE ... WHERE click_count < max_clicks and check the row count - *Check and increment are one atomic statement, so concurrent redirects cannot overshoot the limit.* (rejected: Read-check-write in Java (races), Optimistic locking with retries (contention on hot links))
- **D3** Keep the unlimited path byte-for-byte equivalent and isolate the new branch - *Minimises regression risk for the existing behaviour (FR-4) and keeps analytics failure isolation.* (rejected: Single unified path (simpler code, larger behavioural blast radius))
- **D4** Return 410 Gone (LinkGoneException) when the limit is reached - *Same semantics clients already handle for expired and deactivated links.* (rejected: 404 Not Found (hides that the link existed), 429 (wrong semantics))

## 4. Artifacts

- Patch: `artifacts/implementation-r2.patch` (+171 / -14); final release patch: `release/change.patch`
- Files: `src/main/java/io/agentic/shortener/domain/Link.java`, `src/main/java/io/agentic/shortener/repository/LinkRepository.java`, `src/main/java/io/agentic/shortener/service/CreateLinkCommand.java`, `src/main/java/io/agentic/shortener/service/LinkService.java`, `src/main/java/io/agentic/shortener/web/LinkController.java`, `src/main/java/io/agentic/shortener/web/dto/CreateLinkRequest.java`, `src/main/java/io/agentic/shortener/web/dto/LinkResponse.java`, `src/main/resources/db/migration/V2__add_link_max_clicks.sql`, `src/test/java/io/agentic/shortener/web/MaxClicksIntegrationTest.java`
- Test methods (static count): 25 before -> 30 after
- Run directory: `orchestrator/runs/demo-20261030T164615/brownfield-20261030T164615`
- Audit log: `audit.jsonl` (hash-chained; `agentic audit verify <run>`); metrics: `metrics.json`
- Stage artifacts: `artifacts/`; build logs: `logs/`; snapshots: `snapshots/`

## 5. Traceability (requirement -> task -> code -> test)

| Requirement | Tasks | Files | Verified |
|---|---|---|---|
| FR-1 | T1, T2, T4, T6 | `src/main/java/io/agentic/shortener/domain/Link.java`, `src/main/java/io/agentic/shortener/service/CreateLinkCommand.java`, `src/main/java/io/agentic/shortener/web/LinkController.java`, `src/main/java/io/agentic/shortener/web/dto/CreateLinkRequest.java`, `src/main/java/io/agentic/shortener/web/dto/LinkResponse.java`, `src/main/resources/db/migration/V2__add_link_max_clicks.sql`, `src/test/java/io/agentic/shortener/web/MaxClicksIntegrationTest.java` | yes |
| FR-2 | T3, T5, T6 | `src/main/java/io/agentic/shortener/repository/LinkRepository.java`, `src/main/java/io/agentic/shortener/service/LinkService.java`, `src/test/java/io/agentic/shortener/web/MaxClicksIntegrationTest.java` | yes |
| FR-3 | T2, T4, T6 | `src/main/java/io/agentic/shortener/domain/Link.java`, `src/main/java/io/agentic/shortener/service/CreateLinkCommand.java`, `src/main/java/io/agentic/shortener/web/LinkController.java`, `src/main/java/io/agentic/shortener/web/dto/CreateLinkRequest.java`, `src/main/java/io/agentic/shortener/web/dto/LinkResponse.java`, `src/test/java/io/agentic/shortener/web/MaxClicksIntegrationTest.java` | yes |
| FR-4 | T1, T5, T6 | `src/main/java/io/agentic/shortener/service/LinkService.java`, `src/main/resources/db/migration/V2__add_link_max_clicks.sql`, `src/test/java/io/agentic/shortener/web/MaxClicksIntegrationTest.java` | yes |

## 6. Validation

- **Build/test:** PASSED - 50 tests, 0 failures, 0 errors (`mvn -B -q test`, 8.99s)
- **Rework cycles:** 1
- **Security/compliance findings:** {'block': 0, 'require_approval': 0, 'warn': 0, 'total': 0}
- **Gates:** design=completed, design_approval=completed, documentation=completed, implementation=completed, planning=completed, release=completed, release_approval=completed, requirements=completed, security_review=completed, sync=completed, verification=completed

## 7. Human oversight (every decision, in order)

- **design_approval** by `architect@shortener-team` (pre-recorded scenario decision): approve - Nullable column + new V2 migration keeps existing rows valid. Conditional UPDATE for the limit is the right call for concurrency. Approved.
- **release_approval** by `tech-lead@shortener-team` (pre-recorded scenario decision): approve - Off-by-one from round 1 was caught by tests and fixed; boundary test now covers the limit exactly.

## 8. Risks and trade-offs

- Additive response fields could break clients that use strict deserialisation (unknown properties).
- The limited path records the click event after the counter update; an analytics failure there leaves the counter incremented without an event (counter remains authoritative for the limit).
- Hot single-use links serialise on one row update; acceptable at prototype scale.

## 9. Decision lineage

| # | Stage | Actor | Decision | Rationale |
|---|---|---|---|---|
| requirements#1 | requirements | agent:requirements | Normalised requirement into 4 FR(s) | intent: Allow single-use or limited-use links that stop redirecting after a configured number of clicks. |
| planning#1 | planning | agent:planning | Plan r1: 7 tasks in 6 waves | Schema first (immutable V1, new V2), then the entity, then API/command types that depend on the entity accessors, then the service logic that ties them together |
| design#1 | design | agent:design | D1: New migration V2 adds nullable max_clicks INTEGER with CHECK (max_clicks > 0) | Applied migrations are immutable; NULL means unlimited, so every existing row stays valid (FR-4). |
| design#2 | design | agent:design | D2: Enforce the limit with a conditional UPDATE ... WHERE click_count < max_clicks and check the row count | Check and increment are one atomic statement, so concurrent redirects cannot overshoot the limit. |
| design#3 | design | agent:design | D3: Keep the unlimited path byte-for-byte equivalent and isolate the new branch | Minimises regression risk for the existing behaviour (FR-4) and keeps analytics failure isolation. |
| design#4 | design | agent:design | D4: Return 410 Gone (LinkGoneException) when the limit is reached | Same semantics clients already handle for expired and deactivated links. |
| design#5 | design | agent:design | Risk classified high -> human approval required | schema/data-model change; existing public API modified |
| design_approval#1 | design_approval | human:architect@shortener-team | design_approval: approve | Nullable column + new V2 migration keeps existing rows valid. Conditional UPDATE for the limit is the right call for concurrency. Approved. |
| implementation#1 | implementation | agent:implementation | Change set r1: 9 file(s) | Add maxClicks end-to-end (migration, entity, repository, API, service enforcement) with tests. |
| documentation#1 | documentation | agent:documentation | Documented 4 requirement(s) in docs/features/click-limits.md | docs generated from spec + design, not from code comments |
| security_review#1 | security_review | agent:security_review | Security review: 0 blocking, 0 need approval, 0 warnings | policy-as-code scan of all added lines |
| verification#1 | verification | agent:verification | Build r1 red: 50 tests, 2 failures, 0 errors | mvn -B -q test |
| implementation#2 | implementation | agent:implementation | Change set r2: 9 file(s) | Add maxClicks end-to-end; fix off-by-one from round 1 (conditional UPDATE must use click_count < max_clicks, otherwise a link with maxClicks=N redirects N+1 tim |
| documentation#2 | documentation | agent:documentation | Documented 4 requirement(s) in docs/features/click-limits.md | docs generated from spec + design, not from code comments |
| security_review#2 | security_review | agent:security_review | Security review: 0 blocking, 0 need approval, 0 warnings | policy-as-code scan of all added lines |
| verification#2 | verification | agent:verification | Build r2 green: 50 tests, 0 failures, 0 errors | mvn -B -q test |
| release_approval#1 | release_approval | human:tech-lead@shortener-team | release_approval: approve | Off-by-one from round 1 was caught by tests and fixed; boundary test now covers the limit exactly. |
| release#1 | release | agent:release | Release package with 11 file(s); not promoted | human-approved; patch is the reviewable outcome |

## 10. Reliability metrics

- **run_status:** completed
- **stage_executions:** 12
- **stage_success_rate:** 0.917
- **stage_failures:** 1
- **retries:** 1
- **retry_frequency:** 0.083
- **rollbacks:** 1
- **rollback_frequency:** 0.083
- **fallbacks:** 0
- **replans:** 2
- **safe_stops:** 0
- **approvals:** 2
- **policy_findings:** 0
- **mttr_s:** 9.139
- **recoveries:** 1
- **e2e_latency_s:** 18.579
- **human_wait_s:** 0.005
- **active_latency_s:** 18.574
- **llm_calls:** 7
- **llm_latency_p50_s:** 0.0
- **llm_latency_p95_s:** 0.001
- **retries_by_scope:** {'rework': 1}
- **llm_calls_by_provider:** {'replay': 7}
- **stage_latency_p50_s:** {'design': 0.015, 'documentation': 0.007, 'implementation': 0.072, 'planning': 0.002, 'release': 0.022, 'requirements': 0.005, 'security_review': 0.03, 'verification': 8.996}

## 11. Assumptions

- Deactivation and expiry keep their precedence; the click limit is an additional reason for 410.

## 12. Limitations

- Verification runs the project's own test suite in a sandbox copy; it proves behaviour the tests cover, not behaviour they omit. Human review of the patch remains mandatory (release checkpoint).
- Policy rules are pattern-based (added lines only); they catch known-bad constructs, not novel logic flaws.
- Impact analysis is static and type-level (imports/identifiers); reflection and configuration-driven wiring are not modelled.
- In replay mode the LLM responses are recorded fixtures: orchestration, governance, policy, execution and verification are real, but generation quality is that of the recording, not a live model.
