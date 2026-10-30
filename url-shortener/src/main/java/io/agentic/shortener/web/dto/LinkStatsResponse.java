package io.agentic.shortener.web.dto;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;

public record LinkStatsResponse(
        String code,
        long totalClicks,
        long uniqueVisitors,
        Instant lastClickAt,
        List<DailyCount> clicksByDay,
        List<ReferrerCount> topReferrers) {

    public record DailyCount(LocalDate date, long clicks) {
    }

    public record ReferrerCount(String host, long clicks) {
    }
}
