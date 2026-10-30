package io.agentic.shortener.service;

import java.time.Instant;

/** Service-layer input, decoupled from the HTTP DTO. */
public record CreateLinkCommand(String url, String customAlias, Instant expiresAt, String idempotencyKey) {
}
