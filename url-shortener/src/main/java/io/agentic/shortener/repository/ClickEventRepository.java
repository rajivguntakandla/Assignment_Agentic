package io.agentic.shortener.repository;

import io.agentic.shortener.domain.ClickEvent;
import java.time.Instant;
import java.util.List;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface ClickEventRepository extends JpaRepository<ClickEvent, Long> {

    @Query("select count(distinct c.visitorHash) from ClickEvent c where c.link.id = :linkId")
    long countUniqueVisitors(@Param("linkId") Long linkId);

    @Query("select c.clickedAt from ClickEvent c where c.link.id = :linkId and c.clickedAt >= :since")
    List<Instant> findClickTimesSince(@Param("linkId") Long linkId, @Param("since") Instant since);

    @Query("""
            select c.referrerHost, count(c) from ClickEvent c
            where c.link.id = :linkId and c.referrerHost is not null
            group by c.referrerHost order by count(c) desc, c.referrerHost asc""")
    List<Object[]> topReferrers(@Param("linkId") Long linkId, Pageable page);

    @Query("select max(c.clickedAt) from ClickEvent c where c.link.id = :linkId")
    Instant findLastClick(@Param("linkId") Long linkId);
}
