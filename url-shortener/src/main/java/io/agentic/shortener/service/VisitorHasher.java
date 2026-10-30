package io.agentic.shortener.service;

import io.agentic.shortener.config.ShortenerProperties;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import org.springframework.stereotype.Component;

/** Pseudonymises visitors: salted SHA-256 of address + user agent. Raw values are never persisted. */
@Component
public class VisitorHasher {

    private final byte[] salt;

    public VisitorHasher(ShortenerProperties properties) {
        this.salt = properties.visitorHashSalt().getBytes(StandardCharsets.UTF_8);
    }

    public String hash(String clientAddress, String userAgent) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update(salt);
            digest.update((nullToEmpty(clientAddress) + "|" + nullToEmpty(userAgent)).getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }
}
