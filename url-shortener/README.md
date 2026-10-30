# URL Shortener Service

Spring Boot 3.5 / Java 21 service with core link APIs, click analytics and reliability controls. It is also the
target system that the agentic orchestrator (`../orchestrator`) changes in its greenfield, brownfield and
ambiguous scenarios.

## Run

```bash
mvn spring-boot:run                     # http://localhost:8080, H2 file DB in ./data
mvn -B verify                           # unit + integration tests
```

| Variable | Default | Purpose |
|---|---|---|
| `SHORTENER_BASE_URL` | `http://localhost:8080` | Prefix for `shortUrl` |
| `SHORTENER_VISITOR_SALT` | `change-me-in-production` | Salt for pseudonymous visitor hashes |
| `DATABASE_URL` / `DATABASE_USER` / `DATABASE_PASSWORD` | H2 file DB | Any JDBC database supported by Flyway/Hibernate |

OpenAPI: `GET /v3/api-docs`, Swagger UI: `/swagger-ui.html`, health: `/actuator/health`, metrics: `/actuator/metrics`.

## API

| Method | Path | Result |
|---|---|---|
| `POST` | `/api/v1/links` | `201` created; `200` on an `Idempotency-Key` replay; `400` invalid URL/alias/expiry; `409` alias taken or key reused with a different body; `429` rate limited |
| `GET` | `/{code}` | `302` to the target (`Cache-Control: no-store`); `404` unknown; `410` expired or deactivated |
| `GET` | `/api/v1/links/{code}` | Link metadata incl. `clickCount` |
| `GET` | `/api/v1/links/{code}/stats?days=30` | Total clicks, unique visitors, UTC daily series (1-90 days), top referrers, last click |
| `DELETE` | `/api/v1/links/{code}` | `204`; soft delete, later redirects return `410` |

Errors use RFC 9457 problem details (`application/problem+json`); internal errors never expose stack traces.

```bash
curl -s -X POST localhost:8080/api/v1/links -H 'Content-Type: application/json' \
     -H 'Idempotency-Key: 7f0c' -d '{"url":"https://example.com/docs","customAlias":"docs"}'
curl -si localhost:8080/docs | head -3
curl -s localhost:8080/api/v1/links/docs/stats?days=7
```

## Design notes

| Concern | Decision |
|---|---|
| Codes | 7-char base62 from `SecureRandom` (~3.5e12 space, not enumerable); bounded collision retries |
| URL safety | http/https only; no credentials in URL; loopback, private, link-local and CGNAT literals and `*.internal`/`*.local` rejected (SSRF, open-redirect into internal networks); self-links rejected (loops). No DNS resolution at write time (slow, rebinding-prone) |
| Analytics | One `click_events` row per redirect; the counter is incremented with an atomic `UPDATE` (no lost updates). Visitors stored as salted SHA-256 of IP + user agent only (data minimisation). Analytics failures are logged and counted but never break a redirect |
| Redirect status | `302` + `no-store`, not `301`: browsers would cache a 301, which undercounts clicks and makes expiry and deactivation ineffective |
| Idempotency | `Idempotency-Key` stored on the link (unique); replay returns the original link, a different body returns `409` |
| Rate limiting | Token bucket per client address on writes (`POST`/`DELETE`), bounded LRU key map. Single-node by design; use a shared store (Redis) when scaling out |
| Schema | Flyway migrations, Hibernate `ddl-auto=validate` |
| Time | Injected `Clock` makes expiry and daily buckets deterministic in tests |

## Tests

- `UrlValidatorTest`, `CodeGeneratorTest`, `TokenBucketRateLimiterTest`, `LinkServiceUnitTest`: unit tests.
- `LinkApiIntegrationTest`: full HTTP behaviour through MockMvc with an in-memory DB (create, alias, idempotency,
  validation, expiry with a controllable clock, deactivation, analytics aggregation, health, OpenAPI).
- `RateLimitIntegrationTest`: throttling of writes with `Retry-After`; reads stay unthrottled.
