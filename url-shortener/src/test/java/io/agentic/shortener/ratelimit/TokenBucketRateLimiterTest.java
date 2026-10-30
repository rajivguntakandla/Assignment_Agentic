package io.agentic.shortener.ratelimit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;

class TokenBucketRateLimiterTest {

    private final AtomicLong nanos = new AtomicLong();

    @Test
    void allowsBurstUpToCapacityThenThrottles() {
        var limiter = new TokenBucketRateLimiter(3, 60, 100, nanos::get);
        assertThat(limiter.tryAcquire("a")).isZero();
        assertThat(limiter.tryAcquire("a")).isZero();
        assertThat(limiter.tryAcquire("a")).isZero();
        assertThat(limiter.tryAcquire("a")).isEqualTo(1);
    }

    @Test
    void refillsOverTime() {
        var limiter = new TokenBucketRateLimiter(1, 60, 100, nanos::get);
        assertThat(limiter.tryAcquire("a")).isZero();
        assertThat(limiter.tryAcquire("a")).isPositive();
        nanos.addAndGet(1_000_000_000L);
        assertThat(limiter.tryAcquire("a")).isZero();
    }

    @Test
    void keysAreIsolated() {
        var limiter = new TokenBucketRateLimiter(1, 1, 100, nanos::get);
        assertThat(limiter.tryAcquire("a")).isZero();
        assertThat(limiter.tryAcquire("b")).isZero();
        assertThat(limiter.tryAcquire("a")).isEqualTo(60);
    }

    @Test
    void evictsLeastRecentlyUsedKeysBeyondBound() {
        var limiter = new TokenBucketRateLimiter(1, 1, 2, nanos::get);
        limiter.tryAcquire("a");
        limiter.tryAcquire("b");
        limiter.tryAcquire("c"); // evicts "a"
        assertThat(limiter.tryAcquire("a")).isZero();
    }

    @Test
    void rejectsInvalidConfiguration() {
        assertThatThrownBy(() -> new TokenBucketRateLimiter(0, 1, 1, nanos::get))
                .isInstanceOf(IllegalArgumentException.class);
    }
}
