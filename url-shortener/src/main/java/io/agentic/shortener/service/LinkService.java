package io.agentic.shortener.service;

import io.agentic.shortener.config.ShortenerProperties;
import io.agentic.shortener.domain.ClickEvent;
import io.agentic.shortener.domain.Link;
import io.agentic.shortener.repository.ClickEventRepository;
import io.agentic.shortener.repository.LinkRepository;
import io.micrometer.core.instrument.MeterRegistry;
import java.time.Clock;
import java.time.Instant;
import java.util.Locale;
import java.util.Objects;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class LinkService {

    private static final Logger log = LoggerFactory.getLogger(LinkService.class);
    private static final Pattern ALIAS = Pattern.compile("^[A-Za-z0-9_-]{3,32}$");

    private final LinkRepository links;
    private final ClickEventRepository clicks;
    private final UrlValidator urlValidator;
    private final CodeGenerator codeGenerator;
    private final VisitorHasher visitorHasher;
    private final ShortenerProperties properties;
    private final Clock clock;
    private final MeterRegistry meters;

    public LinkService(LinkRepository links, ClickEventRepository clicks, UrlValidator urlValidator,
                       CodeGenerator codeGenerator, VisitorHasher visitorHasher, ShortenerProperties properties,
                       Clock clock, MeterRegistry meters) {
        this.links = links;
        this.clicks = clicks;
        this.urlValidator = urlValidator;
        this.codeGenerator = codeGenerator;
        this.visitorHasher = visitorHasher;
        this.properties = properties;
        this.clock = clock;
        this.meters = meters;
    }

    @Transactional
    public CreateLinkResult create(CreateLinkCommand command) {
        String targetUrl = urlValidator.validate(command.url()).toString();
        Instant now = clock.instant();
        if (command.expiresAt() != null && !command.expiresAt().isAfter(now)) {
            throw new InvalidUrlException("expiresAt must be in the future");
        }

        if (command.idempotencyKey() != null) {
            var existing = links.findByIdempotencyKey(command.idempotencyKey());
            if (existing.isPresent()) {
                return replay(existing.get(), targetUrl, command);
            }
        }

        String code = command.customAlias() != null ? reserveAlias(command.customAlias()) : generateUniqueCode();
        Link link = new Link(code, targetUrl, now, command.expiresAt(), command.idempotencyKey());
        try {
            link = links.saveAndFlush(link);
        } catch (DataIntegrityViolationException e) {
            // Lost a race on alias or idempotency key between the existence check and the insert.
            throw new ConflictException("Link could not be created because of a concurrent conflicting request");
        }
        meters.counter("shortener.links.created").increment();
        log.info("link.created code={} custom={}", code, command.customAlias() != null);
        return new CreateLinkResult(link, true);
    }

    @Transactional(readOnly = true)
    public Link get(String code) {
        return links.findByCode(code).orElseThrow(() -> new LinkNotFoundException(code));
    }

    /**
     * Resolves a code for redirection and records the click. Analytics failures are isolated so they can never
     * break the redirect path (availability of redirects matters more than completeness of analytics).
     */
    @Transactional
    public String resolveForRedirect(String code, String referrer, String clientAddress, String userAgent) {
        Link link = links.findByCode(code).orElseThrow(() -> {
            meters.counter("shortener.redirects", "outcome", "not_found").increment();
            return new LinkNotFoundException(code);
        });
        Instant now = clock.instant();
        if (!link.isActive()) {
            meters.counter("shortener.redirects", "outcome", "gone").increment();
            throw new LinkGoneException(code, "deactivated");
        }
        if (link.isExpiredAt(now)) {
            meters.counter("shortener.redirects", "outcome", "gone").increment();
            throw new LinkGoneException(code, "expired");
        }
        try {
            clicks.save(new ClickEvent(link, now, referrerHost(referrer), visitorHasher.hash(clientAddress, userAgent)));
            links.incrementClickCount(link.getId());
        } catch (RuntimeException e) {
            log.warn("analytics.record_failed code={} error={}", code, e.toString());
            meters.counter("shortener.analytics.failures").increment();
        }
        meters.counter("shortener.redirects", "outcome", "redirected").increment();
        return link.getTargetUrl();
    }

    @Transactional
    public void deactivate(String code) {
        Link link = get(code);
        link.deactivate();
        log.info("link.deactivated code={}", code);
    }

    private CreateLinkResult replay(Link existing, String targetUrl, CreateLinkCommand command) {
        boolean sameRequest = existing.getTargetUrl().equals(targetUrl)
                && (command.customAlias() == null || command.customAlias().equals(existing.getCode()))
                && Objects.equals(command.expiresAt(), existing.getExpiresAt());
        if (!sameRequest) {
            throw new ConflictException("Idempotency-Key was already used with a different request");
        }
        return new CreateLinkResult(existing, false);
    }

    private String reserveAlias(String alias) {
        if (!ALIAS.matcher(alias).matches()) {
            throw new InvalidUrlException("customAlias must be 3-32 characters of [A-Za-z0-9_-]");
        }
        if (properties.reservedCodes().contains(alias.toLowerCase(Locale.ROOT))) {
            throw new ConflictException("Alias '" + alias + "' is reserved");
        }
        if (links.existsByCode(alias)) {
            throw new ConflictException("Alias '" + alias + "' is already in use");
        }
        return alias;
    }

    private String generateUniqueCode() {
        for (int attempt = 1; attempt <= properties.maxCodeGenerationAttempts(); attempt++) {
            String candidate = codeGenerator.generate(properties.codeLength());
            if (!links.existsByCode(candidate)) {
                return candidate;
            }
            meters.counter("shortener.code.collisions").increment();
        }
        throw new IllegalStateException("Could not generate a unique code; keyspace may be exhausted");
    }

    static String referrerHost(String referrer) {
        if (referrer == null || referrer.isBlank()) {
            return null;
        }
        try {
            String host = java.net.URI.create(referrer.trim()).getHost();
            return host == null ? null : host.toLowerCase(Locale.ROOT);
        } catch (IllegalArgumentException e) {
            return null;
        }
    }
}
