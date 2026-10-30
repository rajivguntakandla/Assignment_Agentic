package io.agentic.shortener.config;

import io.agentic.shortener.ratelimit.RateLimitFilter;
import io.agentic.shortener.ratelimit.TokenBucketRateLimiter;
import java.security.SecureRandom;
import java.time.Clock;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class AppConfig {

    /** Injected everywhere time matters so tests can control expiry and analytics windows. */
    @Bean
    Clock clock() {
        return Clock.systemUTC();
    }

    @Bean
    SecureRandom secureRandom() {
        return new SecureRandom();
    }

    @Bean
    TokenBucketRateLimiter tokenBucketRateLimiter(ShortenerProperties properties) {
        ShortenerProperties.RateLimit cfg = properties.rateLimit();
        return new TokenBucketRateLimiter(cfg.capacity(), cfg.refillPerMinute(), 10_000, System::nanoTime);
    }

    @Bean
    FilterRegistrationBean<RateLimitFilter> rateLimitFilter(TokenBucketRateLimiter limiter,
                                                            ShortenerProperties properties) {
        FilterRegistrationBean<RateLimitFilter> registration =
                new FilterRegistrationBean<>(new RateLimitFilter(limiter, properties.rateLimit().enabled()));
        registration.addUrlPatterns("/api/v1/links", "/api/v1/links/*");
        registration.setOrder(1);
        return registration;
    }
}
