package io.agentic.shortener.service;

import io.agentic.shortener.domain.Link;

/** {@code created == false} means an idempotent replay returned the previously created link. */
public record CreateLinkResult(Link link, boolean created) {
}
