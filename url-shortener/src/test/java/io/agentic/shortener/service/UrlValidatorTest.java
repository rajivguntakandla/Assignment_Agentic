package io.agentic.shortener.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import io.agentic.shortener.config.ShortenerProperties;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class UrlValidatorTest {

    private final UrlValidator validator = new UrlValidator(new ShortenerProperties(
            "https://sho.rt", 7, 100, 5, "salt", List.of(), null));

    @ParameterizedTest
    @ValueSource(strings = {
            "https://example.com",
            "http://example.com/path?q=1#frag",
            "HTTPS://Example.COM/Upper",
            "https://8.8.8.8/dns",
            "  https://example.com/trimmed  "})
    void acceptsPublicHttpUrls(String url) {
        assertThat(validator.validate(url).getHost()).isNotBlank();
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "javascript:alert(1)",
            "ftp://example.com/file",
            "file:///etc/passwd",
            "example.com/no-scheme",
            "https://",
            "https://user:pass@example.com",
            "https://bank.com@evil.example",
            "http://localhost:8080/admin",
            "http://127.0.0.1/",
            "http://10.0.0.5/",
            "http://172.20.1.1/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/",
            "http://service.internal/",
            "https://sho.rt/abc1234",
            "https://exa mple.com"})
    void rejectsUnsafeOrMalformedUrls(String url) {
        assertThatThrownBy(() -> validator.validate(url)).isInstanceOf(InvalidUrlException.class);
    }

    @Test
    void rejectsBlankAndOverlongUrls() {
        assertThatThrownBy(() -> validator.validate(" ")).isInstanceOf(InvalidUrlException.class);
        assertThatThrownBy(() -> validator.validate(null)).isInstanceOf(InvalidUrlException.class);
        assertThatThrownBy(() -> validator.validate("https://example.com/" + "a".repeat(100)))
                .isInstanceOf(InvalidUrlException.class)
                .hasMessageContaining("maximum length");
    }

    @Test
    void classifiesInternalHosts() {
        assertThat(UrlValidator.isInternalHost("172.15.0.1")).isFalse();
        assertThat(UrlValidator.isInternalHost("172.16.0.1")).isTrue();
        assertThat(UrlValidator.isInternalHost("100.64.0.1")).isTrue();
        assertThat(UrlValidator.isInternalHost("example.com")).isFalse();
    }
}
