package io.agentic.shortener.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.time.Instant;

@Schema(description = "Request to shorten a URL")
public record CreateLinkRequest(
        @Schema(example = "https://example.com/some/long/path?q=1")
        @NotBlank @Size(max = 8192) String url,
        @Schema(description = "Optional vanity code", example = "spring-sale")
        @Pattern(regexp = "^[A-Za-z0-9_-]{3,32}$", message = "must be 3-32 characters of [A-Za-z0-9_-]")
        String customAlias,
        @Schema(description = "Optional absolute expiry (ISO-8601, must be in the future)")
        Instant expiresAt) {
}
