package io.agentic.shortener.service;

import io.agentic.shortener.domain.Link;
import io.agentic.shortener.repository.ClickEventRepository;
import io.agentic.shortener.web.dto.LinkStatsResponse;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class AnalyticsService {

    static final int MAX_WINDOW_DAYS = 90;
    private static final int TOP_REFERRERS = 10;

    private final LinkService linkService;
    private final ClickEventRepository clicks;
    private final Clock clock;

    public AnalyticsService(LinkService linkService, ClickEventRepository clicks, Clock clock) {
        this.linkService = linkService;
        this.clicks = clicks;
        this.clock = clock;
    }

    /** Aggregated stats; daily buckets are UTC and bounded to {@value #MAX_WINDOW_DAYS} days. */
    @Transactional(readOnly = true)
    public LinkStatsResponse stats(String code, int windowDays) {
        if (windowDays < 1 || windowDays > MAX_WINDOW_DAYS) {
            throw new InvalidUrlException("days must be between 1 and " + MAX_WINDOW_DAYS);
        }
        Link link = linkService.get(code);
        LocalDate today = LocalDate.ofInstant(clock.instant(), ZoneOffset.UTC);
        LocalDate firstDay = today.minusDays(windowDays - 1L);
        Instant since = firstDay.atStartOfDay(ZoneOffset.UTC).toInstant();

        Map<LocalDate, Long> byDay = new TreeMap<>();
        for (LocalDate d = firstDay; !d.isAfter(today); d = d.plus(1, ChronoUnit.DAYS)) {
            byDay.put(d, 0L);
        }
        for (Instant t : clicks.findClickTimesSince(link.getId(), since)) {
            byDay.merge(LocalDate.ofInstant(t, ZoneOffset.UTC), 1L, Long::sum);
        }

        List<LinkStatsResponse.ReferrerCount> referrers = clicks
                .topReferrers(link.getId(), PageRequest.of(0, TOP_REFERRERS)).stream()
                .map(row -> new LinkStatsResponse.ReferrerCount((String) row[0], ((Number) row[1]).longValue()))
                .toList();

        List<LinkStatsResponse.DailyCount> daily = byDay.entrySet().stream()
                .map(e -> new LinkStatsResponse.DailyCount(e.getKey(), e.getValue()))
                .toList();

        return new LinkStatsResponse(link.getCode(), link.getClickCount(),
                clicks.countUniqueVisitors(link.getId()), clicks.findLastClick(link.getId()), daily, referrers);
    }
}
