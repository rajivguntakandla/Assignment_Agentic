package io.agentic.shortener.repository;

import io.agentic.shortener.domain.Link;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface LinkRepository extends JpaRepository<Link, Long> {

    Optional<Link> findByCode(String code);

    Optional<Link> findByIdempotencyKey(String idempotencyKey);

    boolean existsByCode(String code);

    /** Atomic increment: avoids lost updates under concurrent redirects without optimistic-lock retries. */
    @Modifying(clearAutomatically = true)
    @Query("update Link l set l.clickCount = l.clickCount + 1 where l.id = :id")
    int incrementClickCount(@Param("id") Long id);
}
