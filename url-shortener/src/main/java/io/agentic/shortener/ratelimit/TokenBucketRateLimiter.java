package io.agentic.shortener.ratelimit;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.LongSupplier;

/**
 * In-memory token bucket per client key. Bounded LRU map so a flood of distinct keys cannot exhaust memory.
 *
 * <p>Single-node by design; a horizontally scaled deployment would move this to a shared store (e.g. Redis).
 */
public class TokenBucketRateLimiter {

    private final int capacity;
    private final double tokensPerNano;
    private final LongSupplier nanoClock;
    private final Map<String, Bucket> buckets;

    public TokenBucketRateLimiter(int capacity, int refillPerMinute, int maxTrackedKeys, LongSupplier nanoClock) {
        if (capacity < 1 || refillPerMinute < 1 || maxTrackedKeys < 1) {
            throw new IllegalArgumentException("capacity, refillPerMinute and maxTrackedKeys must be positive");
        }
        this.capacity = capacity;
        this.tokensPerNano = refillPerMinute / 60_000_000_000.0;
        this.nanoClock = nanoClock;
        this.buckets = new LinkedHashMap<>(256, 0.75f, true) {
            @Override
            protected boolean removeEldestEntry(Map.Entry<String, Bucket> eldest) {
                return size() > maxTrackedKeys;
            }
        };
    }

    /** @return 0 if the request is allowed, otherwise the number of seconds until a token is available. */
    public synchronized long tryAcquire(String key) {
        long now = nanoClock.getAsLong();
        Bucket bucket = buckets.computeIfAbsent(key, k -> new Bucket(capacity, now));
        bucket.refill(now, capacity, tokensPerNano);
        if (bucket.tokens >= 1.0) {
            bucket.tokens -= 1.0;
            return 0;
        }
        double nanosUntilToken = (1.0 - bucket.tokens) / tokensPerNano;
        return Math.max(1, (long) Math.ceil(nanosUntilToken / 1_000_000_000.0));
    }

    private static final class Bucket {
        private double tokens;
        private long lastRefill;

        private Bucket(double tokens, long lastRefill) {
            this.tokens = tokens;
            this.lastRefill = lastRefill;
        }

        private void refill(long now, int capacity, double tokensPerNano) {
            long elapsed = now - lastRefill;
            if (elapsed > 0) {
                tokens = Math.min(capacity, tokens + elapsed * tokensPerNano);
                lastRefill = now;
            }
        }
    }
}
