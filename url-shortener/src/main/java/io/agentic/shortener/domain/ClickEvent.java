package io.agentic.shortener.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import java.time.Instant;

/**
 * One redirect. Stores a salted visitor hash instead of raw IP / user agent (data minimisation).
 */
@Entity
@Table(name = "click_events")
public class ClickEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "link_id", nullable = false)
    private Link link;

    @Column(name = "clicked_at", nullable = false)
    private Instant clickedAt;

    @Column(name = "referrer_host")
    private String referrerHost;

    @Column(name = "visitor_hash", nullable = false, length = 64)
    private String visitorHash;

    protected ClickEvent() {
        // for JPA
    }

    public ClickEvent(Link link, Instant clickedAt, String referrerHost, String visitorHash) {
        this.link = link;
        this.clickedAt = clickedAt;
        this.referrerHost = referrerHost;
        this.visitorHash = visitorHash;
    }

    public Instant getClickedAt() {
        return clickedAt;
    }

    public String getReferrerHost() {
        return referrerHost;
    }

    public String getVisitorHash() {
        return visitorHash;
    }
}
