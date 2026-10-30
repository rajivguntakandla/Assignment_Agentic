# Scenarios

Three required scenarios (greenfield, brownfield, ambiguous) plus two drills (governance, resilience). Each one
ran end to end against the real Maven build. Outputs are in [`evidence/`](evidence/README.md): engineering summary,
patch, traceability, audit log, metrics and build logs.

Reproduce everything with `make demo`, or a single scenario with:

```bash
cd orchestrator
.venv/bin/agentic run --scenario <greenfield|brownfield|ambiguous|policy-violation> --llm offline --approvals auto
```

`--approvals auto` replays the human decisions recorded in the scenario file, labelled `pre-recorded scenario
decision` in the audit log. Use `--approvals manual` (the default) to pause at each checkpoint and decide with
`agentic resume <run> --decision ... --approver <you>`.

---

## 1. Greenfield: bulk link creation API

**Requirement:** create up to 100 links per request with independent per-item results (HTTP 200); reject 0 or more
than 100 items with HTTP 400.

**Understanding.** No vague terms and measurable criteria (100, HTTP codes), so no clarification is needed.
Normalised into FR-1 (endpoint and shape), FR-2 (independent items, partial success) and FR-3 (batch-level 400),
each with acceptance criteria. Out of scope: async batches, bulk delete.

**Decomposition** (task DAG; waves are computed, not declared):

| Wave | Tasks |
|---|---|
| 1 | T1 request DTO (FR-1, FR-3), T2 response DTO (FR-1, FR-2), independent and in parallel |
| 2 | T3 `BulkLinkController` -> reuses `LinkService.create` per item (depends on T1, T2) |
| 3 | T4 MockMvc integration tests |
| 4 | T5 docs |

**Orchestration path:** `requirements -> planning -> design -> implementation -> {verification || security_review ||
documentation} -> sync -> release_approval -> release`.
Design risk is **medium** (additive endpoint, no schema or security-sensitive files), so the design-approval
checkpoint is skipped. The release approval (final human quality control) always applies.

**Key decisions:** reuse `LinkService` (single source of validation), one transaction per item (partial success),
200 + per-item status rather than 207, `@Size(1..100)` without cascading `@Valid` (item errors are reported per
item).

**Validation:** 49 tests green (45 existing + 4 new). The security review raised one *warn*
(SEC-200 new endpoint), recorded for the reviewer. Traceability: every FR maps to tasks, files and tests.

---

## 2. Brownfield: click limits on existing links

**Requirement:** optional `maxClicks` (1..1,000,000); after N redirects return 410; expose `maxClicks` and
`remainingClicks`; unlimited links unchanged.

**Codebase reasoning** (static analysis, `codebase.py`). Seeds from domain terms -> `Link`, `LinkService`,
`LinkRepository`, `RedirectController`, ... Reverse-dependency closure gives the impacted modules
`domain, repository, service, web` and tables `links, click_events`. Data flows:

```
RedirectController -> LinkService -> ClickEventRepository -> LinkRepository -> tables(click_events, links)
LinkController -> AnalyticsService -> LinkService -> ClickEventRepository -> LinkRepository -> tables(...)
```

**Risk:** **high**: schema/data-model change *and* an existing public controller modified. The design-approval
checkpoint is mandatory, and the LLM cannot downgrade the classification.

**Decomposition:** strictly sequential because each layer compiles against the previous one: T1 migration
`V2__add_link_max_clicks.sql` (V1 is immutable by policy) -> T2 entity -> T3 conditional `UPDATE` in the repository
and T4 API/command types -> T5 service enforcement -> T6 tests -> T7 docs.

**Key decisions:** nullable column + CHECK constraint (backward compatible). Enforcement by a single
`UPDATE ... WHERE click_count < max_clicks` (race-free; read-check-write was rejected). Unlimited path unchanged.
410 for consistency with expiry.

**What actually happened (non-linear recovery):**

1. Round 1 change set contained a realistic off-by-one (`<=` instead of `<`).
2. Verification ran the real suite: **2 failures** (`Status expected:<410> but was:<302>`, see
   `evidence/brownfield/verification-r1.log`).
3. `sync` barrier -> **rollback** to the pre-implementation snapshot -> **replan** event invalidating
   implementation and every downstream stage -> failing tests fed back to the implementation agent.
4. Round 2 fixed the operator; **50 tests green**; release approved by a human.

MTTR for this recovery is recorded in `evidence/brownfield/metrics.json`.

---

## 3. Ambiguous: "make our short links safer"

**Requirement (verbatim):** "Make our short links safer for users - we keep getting complaints about bad links."

**Understanding.** The deterministic ambiguity analyser flags `safer` and `bad` (vague) plus `acceptance` (no
measurable criteria), each with a question and a proposed default. The requirements agent independently agrees.
Guardrail: the analyser can only *add* ambiguity, so a model cannot argue its way past a human. Result:
`needs_clarification=true`, and the graph routes to the **clarification checkpoint**.

**Human clarification** (product owner): operator-maintained domain denylist incl. subdomains, case-insensitive; 400
with problem detail; look-alikes unaffected; no retroactive changes.

**Re-plan.** Requirements re-run with the answers -> revision 2 (FR-1 config, FR-2 reject, FR-3 look-alikes
allowed). Its output fingerprint changed, so a `replan` event is recorded, and planning and design run on the new
spec.

**Decomposition:** wave 1 = T1 `ShortenerProperties.blockedDomains` and T2 `application.yml` default (parallel);
wave 2 = T3 `UrlValidator` check; wave 3 = T4 tests; wave 4 = T5 docs.

**Design review loop.** `UrlValidator` and `config/` are security-sensitive, so risk is high and design approval is
required.

- Revision 1 used `host.endsWith(blocked)`. The **security architect rejected** it: `notevil.example` would be
  blocked.
- The rejection comment became design feedback -> revision 2 matches on DNS label boundaries (`host == d ||
  host.endsWith("." + d)`) with normalisation. Its decisions *supersede* revision 1 in the lineage.
- Approved.

**Validation:** 55 tests green, including look-alike cases. The `application.yml` change is an approval-path finding
(CC-005, `require_approval`). The release approver had to **explicitly acknowledge** it; an approval without the
acknowledgement fails closed.

---

## 4. Governance drill: policy violation

**Requirement:** an admin endpoint that returns the output of the host's `uptime` command.

The generated controller uses `new ProcessBuilder(...)` -> **SEC-001 (block)**. Sequence: policy evaluation ->
rollback -> retry with the finding fed back -> same violation -> attempt budget exhausted -> **halted**. The workspace
is restored to the baseline hash, a forensic copy is kept (`snapshots/failed-state`), and verification never runs.
The halt is the expected, correct outcome; it is why the aggregate run success rate is 0.8 (4/5).

## 5. Resilience drill

The greenfield scenario with injected faults: 2 transient LLM errors in planning (retried with backoff), malformed
model output in design (rejected by schema validation, corrected via feedback), a provider outage in documentation
(deterministic fallback), a safe-stop before the parallel validation branches (durable pause, operator continues),
and a process crash in `release` (`agentic resume` continues from the SQLite checkpoint without re-executing
completed stages). The run completes. Retries, fallback, safe stops and MTTR are visible in
`evidence/resilience-drill/metrics.json` and its audit log.

## 6. Live run with Claude (no recordings)

To show the system working with a real model rather than recorded responses, an ad-hoc requirement with no
recordings was run with `--llm claude`, so Claude (`claude-opus-5-5`) was the **only** provider:

> Add GET /api/v1/links listing the most recently created links, newest first; optional limit (default 20, 1..100,
> otherwise 400 problem detail); items shaped like GET /api/v1/links/{code}.

What happened (`evidence/live-claude/`):

1. **Requirements**: Claude produced FR-1..FR-4 and raised **2 real clarification questions** (bare array vs.
   envelope; whether expired/deactivated links are included). The run paused at the clarification checkpoint.
2. **Design rev 1**, high risk because an existing controller changes, was **rejected in review** for two real
   defects: a visibility filter that contradicts how `GET /api/v1/links/{code}` behaves, and error handling that the
   service's catch-all exception handler would turn into a 500. The review comment went back to Claude.
3. **Design rev 2** fixed both and cut the scope to 4 files, and was approved.
4. **Implementation**: the first answer failed the traceability validator (a plan task with no change) and was
   corrected through feedback. The policy scan found no blocking findings.
5. **Verification**: real Maven build, **67 tests, 0 failures** (45 existing + 22 written by Claude, covering
   ordering, tie-break, limits, rejections, visibility parity and JSON-shape parity).
6. Release approved and packaged; audit chain valid; 7 Claude calls, 0 fallbacks.

The human decisions in this run were made by the operator on the user's instruction to run it end to end, and are
labelled "Delegated" in the audit log.
