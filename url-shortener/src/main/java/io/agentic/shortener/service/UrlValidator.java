package io.agentic.shortener.service;

import io.agentic.shortener.config.ShortenerProperties;
import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;
import org.springframework.stereotype.Component;

/**
 * Validates target URLs before they are stored.
 *
 * <p>Guards against: non-web schemes ({@code javascript:}, {@code file:}), credential-bearing URLs used for
 * phishing ({@code https://bank.com@evil.example}), redirects into internal networks (loopback, private and
 * link-local literals), and redirect loops back into this service. Hostnames are not DNS-resolved, by design:
 * resolution at write time is both slow and bypassable via DNS rebinding.
 */
@Component
public class UrlValidator {

    private static final Set<String> ALLOWED_SCHEMES = Set.of("http", "https");
    private static final Pattern IPV4 = Pattern.compile("^(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})$");

    private final ShortenerProperties properties;
    private final String ownHost;

    public UrlValidator(ShortenerProperties properties) {
        this.properties = properties;
        this.ownHost = URI.create(properties.baseUrl()).getHost().toLowerCase(Locale.ROOT);
    }

    public URI validate(String rawUrl) {
        if (rawUrl == null || rawUrl.isBlank()) {
            throw new InvalidUrlException("URL must not be blank");
        }
        String trimmed = rawUrl.trim();
        if (trimmed.length() > properties.maxUrlLength()) {
            throw new InvalidUrlException("URL exceeds maximum length of " + properties.maxUrlLength());
        }
        URI uri;
        try {
            uri = new URI(trimmed);
        } catch (URISyntaxException e) {
            throw new InvalidUrlException("URL is not well formed");
        }
        String scheme = uri.getScheme();
        if (scheme == null || !ALLOWED_SCHEMES.contains(scheme.toLowerCase(Locale.ROOT))) {
            throw new InvalidUrlException("Only http and https URLs are supported");
        }
        if (uri.getRawUserInfo() != null) {
            throw new InvalidUrlException("URLs containing credentials are not allowed");
        }
        String host = uri.getHost();
        if (host == null || host.isBlank()) {
            throw new InvalidUrlException("URL must include a host");
        }
        host = host.toLowerCase(Locale.ROOT);
        if (isInternalHost(host)) {
            throw new InvalidUrlException("URLs pointing to internal or loopback addresses are not allowed");
        }
        if (host.equals(ownHost)) {
            throw new InvalidUrlException("URLs pointing back to this shortener are not allowed");
        }
        return uri;
    }

    static boolean isInternalHost(String host) {
        if (host.equals("localhost") || host.endsWith(".localhost") || host.endsWith(".internal")
                || host.endsWith(".local")) {
            return true;
        }
        if (host.startsWith("[")) {
            String v6 = host.substring(1, host.length() - 1);
            return v6.equals("::1") || v6.equals("::") || v6.startsWith("fc") || v6.startsWith("fd")
                    || v6.startsWith("fe80");
        }
        var m = IPV4.matcher(host);
        if (!m.matches()) {
            return false;
        }
        int a = Integer.parseInt(m.group(1));
        int b = Integer.parseInt(m.group(2));
        return a == 0 || a == 10 || a == 127
                || (a == 169 && b == 254)
                || (a == 172 && b >= 16 && b <= 31)
                || (a == 192 && b == 168)
                || (a == 100 && b >= 64 && b <= 127);
    }
}
