# Engineering Summary - Safer short links

- **Run:** `ambiguous-20261030T164615`  
- **Scenario:** ambiguous (`ambiguous`)  
- **Outcome:** READY FOR MERGE (human-approved)  
- **LLM providers (in fallback order):** replay  
- **Execution path:** requirements -> clarification -> requirements -> planning -> design -> design_approval -> design -> design_approval -> implementation -> documentation -> security_review -> verification -> sync -> release_approval -> release

## 1. Requirement understanding

**Intent:** Refuse to shorten URLs whose host is on an operator-maintained denylist (or a subdomain of one).

- **FR-1** A configurable denylist shortener.blocked-domains (empty by default) defines blocked domains.
  - AC: With no configuration, no domain is blocked and existing behaviour is unchanged.
  - AC: Entries are normalised (trimmed, lower-cased, trailing dot removed).
- **FR-2** Creating a link whose host equals a blocked domain or is a subdomain of it is rejected with HTTP 400.
  - AC: With evil.example blocked, https://login.evil.example/reset returns 400 with a problem detail.
  - AC: Matching is case-insensitive (EVIL.example is blocked).
- **FR-3** Domains that merely contain a blocked name are unaffected; existing links are not modified.
  - AC: With evil.example blocked, notevil.example and evil.example.com can still be shortened (201).

**Non-functional:** Validation adds no network calls (no DNS or reputation lookups) on the create path.

**Clarifications (human):** safer: Prevent creating short links to destinations on an operator-maintained denylist of domains; subdomains of a listed domain are blocked too and matching is case-insensitive.; bad: A domain listed in the shortener.blocked-domains configuration property (empty by default). No external reputation service in this iteration.; acceptance: Creating a link to a blocked domain or any of its subdomains returns HTTP 400 with a problem detail; other domains, including look-alikes such as notevil.example for evil.example, are unaffected; existing links are not changed retroactively.

**Out of scope:** External reputation feeds (e.g. Safe Browsing); Retroactive deactivation of existing links

## 2. Plan and rationale

Configuration (property + default) is independent and goes first in parallel; validator logic depends on the property; tests depend on both; docs last.

| Task | Kind | Depends on | Requirements | Files |
|---|---|---|---|---|
| T1 Add blockedDomains to ShortenerProperties with normalisation | config | - | FR-1 | `src/main/java/io/agentic/shortener/config/ShortenerProperties.java` |
| T2 Default empty denylist in application.yml | config | - | FR-1 | `src/main/resources/application.yml` |
| T3 Label-boundary domain check in UrlValidator | code | T1 | FR-2, FR-3 | `src/main/java/io/agentic/shortener/service/UrlValidator.java` |
| T4 Unit + integration tests incl. look-alike domains | test | T2, T3 | FR-1, FR-2, FR-3 | `src/test/java/io/agentic/shortener/service/UrlValidatorTest.java`, `src/test/java/io/agentic/shortener/web/BlockedDomainIntegrationTest.java` |
| T5 Feature documentation and changelog | docs | T4 | - |  |

**Execution waves:** T1, T2 | T3 | T4 | T5

## 3. Architecture and codebase reasoning

Add shortener.blocked-domains (normalised at bind time) and reject hosts that equal a blocked domain or end with "." + blocked domain, i.e. matching on DNS label boundaries. Reuses InvalidUrlException -> 400.

- **Risk:** high (security-sensitive component modified) - human approval required
- **Impacted modules:** config, service, web
- **Affected endpoints:** DELETE /api/v1/links/{code}, GET /api/v1/links/{code}, GET /api/v1/links/{code}/stats, GET /{code:[A-Za-z0-9_-]{3,32}}, POST /api/v1/links
- **Tables:** click_events, links
- **Data flow:** LinkController -> AnalyticsService -> LinkService -> ClickEventRepository -> LinkRepository -> tables(click_events, links)
- **Data flow:** RedirectController -> LinkService -> ClickEventRepository -> LinkRepository -> tables(click_events, links)

- **D1** Enforce the denylist in UrlValidator (single validation choke point for every create path) - *All creation paths already call UrlValidator; no new wiring and no duplicated checks.* (rejected: Servlet filter (sees raw JSON, wrong layer), Separate service (extra wiring))
- **D2** Match host == d or host.endsWith("." + d) (DNS label boundary), after lower-casing and trimming a trailing dot - *Blocks every subdomain while leaving look-alikes such as notevil.example untouched (review feedback); normalisation prevents EVIL.example and trailing-dot bypasses.* (rejected: Plain endsWith (false positives on look-alikes), Public-suffix aware registrable-domain matching (needs a dependency))
- **D3** Normalise entries once in the configuration record's compact constructor - *The hot path compares pre-normalised strings; invalid/blank entries are dropped at startup.* (rejected: Normalise on every request)
- **D4** No DNS resolution or external lookups - *Keeps the create path fast and deterministic; DNS answers are attacker-controlled anyway.* (rejected: Resolve and block IP ranges (slow, rebinding-prone))

## 4. Artifacts

- Patch: `artifacts/implementation-r1.patch` (+97 / -2); final release patch: `release/change.patch`
- Files: `src/main/java/io/agentic/shortener/config/ShortenerProperties.java`, `src/main/java/io/agentic/shortener/service/UrlValidator.java`, `src/main/resources/application.yml`, `src/test/java/io/agentic/shortener/service/UrlValidatorTest.java`, `src/test/java/io/agentic/shortener/web/BlockedDomainIntegrationTest.java`
- Test methods (static count): 25 before -> 30 after
- Run directory: `orchestrator/runs/demo-20261030T164615/ambiguous-20261030T164615`
- Audit log: `audit.jsonl` (hash-chained; `agentic audit verify <run>`); metrics: `metrics.json`
- Stage artifacts: `artifacts/`; build logs: `logs/`; snapshots: `snapshots/`

## 5. Traceability (requirement -> task -> code -> test)

| Requirement | Tasks | Files | Verified |
|---|---|---|---|
| FR-1 | T1, T2, T4 | `src/main/java/io/agentic/shortener/config/ShortenerProperties.java`, `src/main/resources/application.yml`, `src/test/java/io/agentic/shortener/service/UrlValidatorTest.java`, `src/test/java/io/agentic/shortener/web/BlockedDomainIntegrationTest.java` | yes |
| FR-2 | T3, T4 | `src/main/java/io/agentic/shortener/service/UrlValidator.java`, `src/test/java/io/agentic/shortener/service/UrlValidatorTest.java`, `src/test/java/io/agentic/shortener/web/BlockedDomainIntegrationTest.java` | yes |
| FR-3 | T3, T4 | `src/main/java/io/agentic/shortener/service/UrlValidator.java`, `src/test/java/io/agentic/shortener/service/UrlValidatorTest.java`, `src/test/java/io/agentic/shortener/web/BlockedDomainIntegrationTest.java` | yes |

## 6. Validation

- **Build/test:** PASSED - 55 tests, 0 failures, 0 errors (`mvn -B -q test`, 8.88s)
- **Rework cycles:** 0
- **Security/compliance findings:** {'block': 0, 'require_approval': 0, 'warn': 0, 'total': 0}
- **Gates:** clarification=completed, design=completed, design_approval=completed, documentation=completed, implementation=completed, planning=completed, release=completed, release_approval=completed, requirements=completed, security_review=completed, sync=completed, verification=completed

## 7. Human oversight (every decision, in order)

- **clarification** by `product-owner@shortener-team` (pre-recorded scenario decision): approve - human checkpoint
- **design_approval** by `security-architect@shortener-team` (pre-recorded scenario decision): reject - Suffix matching must respect DNS label boundaries: with evil.example blocked, notevil.example must stay allowed. Make the rule explicit in the design and cover it with tests.
- **design_approval** by `security-architect@shortener-team` (pre-recorded scenario decision): approve - Label-boundary matching and normalisation are now explicit and tested. Approved.
- **release_approval** by `tech-lead@shortener-team` (pre-recorded scenario decision): approve - application.yml change acknowledged (adds an empty default denylist only). Validator change reviewed.

## 8. Risks and trade-offs

- Denylists are reactive; new malicious domains pass until listed.
- Existing links to newly blocked domains keep redirecting (explicitly out of scope).
- Changing the list requires a restart (no hot reload in this iteration).

## 9. Decision lineage

| # | Stage | Actor | Decision | Rationale |
|---|---|---|---|---|
| requirements#1 | requirements | agent:requirements | Normalised requirement into 1 FR(s) | intent: Reduce user harm from short links that lead to malicious or unwanted destinations. |
| requirements#2 | requirements | agent:requirements | Escalated 3 ambiguity(ies) to a human | safer; bad; acceptance |
| clarification#1 | clarification | human:product-owner@shortener-team | clarification: approve | human checkpoint |
| requirements#3 | requirements | agent:requirements | Normalised requirement into 3 FR(s) | intent: Refuse to shorten URLs whose host is on an operator-maintained denylist (or a subdomain of one). |
| planning#1 | planning | agent:planning | Plan r1: 5 tasks in 4 waves | Configuration (property + default) is independent and goes first in parallel; validator logic depends on the property; tests depend on both; docs last. |
| design#1 | design | agent:design | D1: Enforce the denylist in UrlValidator (single validation choke point for every create path) | All creation paths already call UrlValidator; no new wiring and no duplicated checks. |
| design#2 | design | agent:design | D2: Match with host.endsWith(blockedDomain) | Covers subdomains with a single comparison. |
| design#3 | design | agent:design | Risk classified high -> human approval required | security-sensitive component modified |
| design_approval#1 | design_approval | human:security-architect@shortener-team | design_approval: reject | Suffix matching must respect DNS label boundaries: with evil.example blocked, notevil.example must stay allowed. Make the rule explicit in the design and cover  |
| design#4 | design | agent:design | D1: Enforce the denylist in UrlValidator (single validation choke point for every create path) | All creation paths already call UrlValidator; no new wiring and no duplicated checks. |
| design#5 | design | agent:design | D2: Match host == d or host.endsWith("." + d) (DNS label boundary), after lower-casing and trimming a trailing dot | Blocks every subdomain while leaving look-alikes such as notevil.example untouched (review feedback); normalisation prevents EVIL.example and trailing-dot bypas |
| design#6 | design | agent:design | D3: Normalise entries once in the configuration record's compact constructor | The hot path compares pre-normalised strings; invalid/blank entries are dropped at startup. |
| design#7 | design | agent:design | D4: No DNS resolution or external lookups | Keeps the create path fast and deterministic; DNS answers are attacker-controlled anyway. |
| design#8 | design | agent:design | Risk classified high -> human approval required | security-sensitive component modified |
| design_approval#2 | design_approval | human:security-architect@shortener-team | design_approval: approve | Label-boundary matching and normalisation are now explicit and tested. Approved. |
| implementation#1 | implementation | agent:implementation | Change set r1: 5 file(s) | Configurable domain denylist with label-boundary matching enforced in UrlValidator, with tests. |
| documentation#1 | documentation | agent:documentation | Documented 3 requirement(s) in docs/features/domain-denylist.md | docs generated from spec + design, not from code comments |
| security_review#1 | security_review | agent:security_review | Security review: 0 blocking, 0 need approval, 0 warnings | policy-as-code scan of all added lines |
| verification#1 | verification | agent:verification | Build r1 green: 55 tests, 0 failures, 0 errors | mvn -B -q test |
| release_approval#1 | release_approval | human:tech-lead@shortener-team | release_approval: approve | application.yml change acknowledged (adds an empty default denylist only). Validator change reviewed. |
| release#1 | release | agent:release | Release package with 7 file(s); not promoted | human-approved; patch is the reviewable outcome |

## 10. Reliability metrics

- **run_status:** completed
- **stage_executions:** 10
- **stage_success_rate:** 1.0
- **stage_failures:** 0
- **retries:** 0
- **retry_frequency:** 0.0
- **rollbacks:** 0
- **rollback_frequency:** 0.0
- **fallbacks:** 0
- **replans:** 1
- **safe_stops:** 0
- **approvals:** 4
- **policy_findings:** 1
- **mttr_s:** None
- **recoveries:** 0
- **e2e_latency_s:** 9.094
- **human_wait_s:** 0.009
- **active_latency_s:** 9.085
- **llm_calls:** 7
- **llm_latency_p50_s:** 0.0
- **llm_latency_p95_s:** 0.0
- **llm_calls_by_provider:** {'replay': 7}
- **stage_latency_p50_s:** {'design': 0.015, 'documentation': 0.008, 'implementation': 0.077, 'planning': 0.002, 'release': 0.043, 'requirements': 0.002, 'security_review': 0.026, 'verification': 8.885}

## 11. Assumptions

- Redirects of already-created links are out of scope (no retroactive blocking).
- Clarified 'safer': Prevent creating short links to destinations on an operator-maintained denylist of domains; subdomains of a listed domain are blocked too and matching is case-insensitive.
- Clarified 'bad': A domain listed in the shortener.blocked-domains configuration property (empty by default). No external reputation service in this iteration.
- Clarified 'acceptance': Creating a link to a blocked domain or any of its subdomains returns HTTP 400 with a problem detail; other domains, including look-alikes such as notevil.example for evil.example, are unaffected; existing links are not changed retroactively.

## 12. Limitations

- Verification runs the project's own test suite in a sandbox copy; it proves behaviour the tests cover, not behaviour they omit. Human review of the patch remains mandatory (release checkpoint).
- Policy rules are pattern-based (added lines only); they catch known-bad constructs, not novel logic flaws.
- Impact analysis is static and type-level (imports/identifiers); reflection and configuration-driven wiring are not modelled.
- In replay mode the LLM responses are recorded fixtures: orchestration, governance, policy, execution and verification are real, but generation quality is that of the recording, not a live model.
