package io.agentic.shortener.service;

import java.security.SecureRandom;
import org.springframework.stereotype.Component;

/** Unpredictable base62 short codes (codes are not enumerable, unlike sequential IDs). */
@Component
public class CodeGenerator {

    static final String ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";

    private final SecureRandom random;

    public CodeGenerator(SecureRandom random) {
        this.random = random;
    }

    public String generate(int length) {
        if (length <= 0) {
            throw new IllegalArgumentException("length must be positive");
        }
        StringBuilder sb = new StringBuilder(length);
        for (int i = 0; i < length; i++) {
            sb.append(ALPHABET.charAt(random.nextInt(ALPHABET.length())));
        }
        return sb.toString();
    }
}
