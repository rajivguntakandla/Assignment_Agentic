package io.agentic.shortener.config;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

/**
 * Externalised service configuration. Bound from the {@code shortener.*} namespace.
 */
@Validated
@ConfigurationProperties(prefix = "shortener")
public record ShortenerProperties(
        @NotBlank String baseUrl,
        @Min(5) @Max(16) int codeLength,
        @Min(16) @Max(8192) int maxUrlLength,
        @Min(1) @Max(10) int maxCodeGenerationAttempts,
        @NotBlank String visitorHashSalt,
        List<String> reservedCodes,
        RateLimit rateLimit) {

    public ShortenerProperties {
        reservedCodes = reservedCodes == null ? List.of() : List.copyOf(reservedCodes);
        rateLimit = rateLimit == null ? new RateLimit(true, 20, 20) : rateLimit;
    }

    /** Token-bucket settings applied to link creation, per client address. */
    public record RateLimit(boolean enabled, @Min(1) int capacity, @Min(1) int refillPerMinute) {
    }
}
