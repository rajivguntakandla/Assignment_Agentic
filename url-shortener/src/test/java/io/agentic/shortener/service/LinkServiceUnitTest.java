package io.agentic.shortener.service;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class LinkServiceUnitTest {

    @Test
    void extractsReferrerHost() {
        assertThat(LinkService.referrerHost("https://News.Example.com/a?b=c")).isEqualTo("news.example.com");
        assertThat(LinkService.referrerHost(null)).isNull();
        assertThat(LinkService.referrerHost(" ")).isNull();
        assertThat(LinkService.referrerHost("not a uri")).isNull();
        assertThat(LinkService.referrerHost("relative/path")).isNull();
    }
}
