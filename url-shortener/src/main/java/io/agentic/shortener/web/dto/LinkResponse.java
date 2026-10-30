package io.agentic.shortener.web.dto;

import io.agentic.shortener.domain.Link;
import java.time.Instant;

public record LinkResponse(
        String code,
        String shortUrl,
        String targetUrl,
        Instant createdAt,
        Instant expiresAt,
        boolean active,
        long clickCount) {

    public static LinkResponse from(Link link, String baseUrl) {
        String base = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        return new LinkResponse(link.getCode(), base + "/" + link.getCode(), link.getTargetUrl(),
                link.getCreatedAt(), link.getExpiresAt(), link.isActive(), link.getClickCount());
    }
}
