package io.agentic.shortener.web;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.hasSize;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.agentic.shortener.MutableClock;
import io.agentic.shortener.TestClockConfig;
import java.time.Duration;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
@Import(TestClockConfig.class)
class LinkApiIntegrationTest {

    @Autowired
    MockMvc mvc;

    @Autowired
    ObjectMapper json;

    @Autowired
    MutableClock clock;

    @BeforeEach
    void resetClock() {
        clock.set(TestClockConfig.START);
    }

    @Test
    void createsLinkAndRedirects() throws Exception {
        JsonNode created = body(create("{\"url\":\"https://example.com/docs\"}")
                .andExpect(status().isCreated())
                .andExpect(header().string("Location", containsString("/api/v1/links/")))
                .andExpect(jsonPath("$.targetUrl").value("https://example.com/docs")));
        String code = created.get("code").asText();
        assertThat(code).matches("[0-9A-Za-z]{7}");
        assertThat(created.get("shortUrl").asText()).isEqualTo("http://sho.rt/" + code);

        mvc.perform(get("/" + code))
                .andExpect(status().isFound())
                .andExpect(header().string("Location", "https://example.com/docs"))
                .andExpect(header().string("Cache-Control", "no-store"));

        mvc.perform(get("/api/v1/links/" + code))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.clickCount").value(1));
    }

    @Test
    void supportsCustomAliasAndRejectsDuplicates() throws Exception {
        create("{\"url\":\"https://example.com\",\"customAlias\":\"my-alias\"}")
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.code").value("my-alias"));
        create("{\"url\":\"https://example.org\",\"customAlias\":\"my-alias\"}")
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.title").value("Conflict"));
    }

    @Test
    void rejectsReservedAndMalformedAliases() throws Exception {
        create("{\"url\":\"https://example.com\",\"customAlias\":\"api\"}").andExpect(status().isConflict());
        create("{\"url\":\"https://example.com\",\"customAlias\":\"a b\"}").andExpect(status().isBadRequest());
    }

    @Test
    void rejectsUnsafeUrlsWithProblemDetail() throws Exception {
        create("{\"url\":\"javascript:alert(1)\"}")
                .andExpect(status().isBadRequest())
                .andExpect(header().string("Content-Type", containsString("application/problem+json")))
                .andExpect(jsonPath("$.detail").value("Only http and https URLs are supported"));
        create("{\"url\":\"http://169.254.169.254/\"}").andExpect(status().isBadRequest());
        create("{}").andExpect(status().isBadRequest()).andExpect(jsonPath("$.detail", containsString("url")));
        create("not json").andExpect(status().isBadRequest());
    }

    @Test
    void expiredLinksReturnGone() throws Exception {
        String code = body(create("{\"url\":\"https://example.com\",\"expiresAt\":\"2026-01-15T11:00:00Z\"}")
                .andExpect(status().isCreated())).get("code").asText();
        mvc.perform(get("/" + code)).andExpect(status().isFound());
        clock.advance(Duration.ofHours(2));
        mvc.perform(get("/" + code)).andExpect(status().isGone());
    }

    @Test
    void rejectsExpiryInThePast() throws Exception {
        create("{\"url\":\"https://example.com\",\"expiresAt\":\"2020-01-01T00:00:00Z\"}")
                .andExpect(status().isBadRequest());
    }

    @Test
    void unknownCodeReturnsNotFound() throws Exception {
        mvc.perform(get("/doesNotExist")).andExpect(status().isNotFound());
        mvc.perform(get("/api/v1/links/doesNotExist")).andExpect(status().isNotFound());
    }

    @Test
    void deactivatedLinksReturnGone() throws Exception {
        String code = body(create("{\"url\":\"https://example.com\"}")).get("code").asText();
        mvc.perform(delete("/api/v1/links/" + code)).andExpect(status().isNoContent());
        mvc.perform(get("/" + code)).andExpect(status().isGone());
        mvc.perform(get("/api/v1/links/" + code)).andExpect(jsonPath("$.active").value(false));
    }

    @Test
    void idempotencyKeyReplaysAndDetectsMisuse() throws Exception {
        String first = body(mvc.perform(post("/api/v1/links").header("Idempotency-Key", "key-1")
                .contentType(MediaType.APPLICATION_JSON).content("{\"url\":\"https://example.com/a\"}"))
                .andExpect(status().isCreated())).get("code").asText();

        mvc.perform(post("/api/v1/links").header("Idempotency-Key", "key-1")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"url\":\"https://example.com/a\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(first));

        mvc.perform(post("/api/v1/links").header("Idempotency-Key", "key-1")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"url\":\"https://example.com/b\"}"))
                .andExpect(status().isConflict());
    }

    @Test
    void statsAggregateClicksVisitorsDaysAndReferrers() throws Exception {
        String code = body(create("{\"url\":\"https://example.com\"}")).get("code").asText();

        click(code, "1.1.1.1", "https://news.example.com/item");
        click(code, "1.1.1.1", "https://news.example.com/other");
        clock.advance(Duration.ofDays(1));
        click(code, "2.2.2.2", "https://blog.example.org/");
        click(code, "3.3.3.3", null);

        mvc.perform(get("/api/v1/links/" + code + "/stats").param("days", "7"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalClicks").value(4))
                .andExpect(jsonPath("$.uniqueVisitors").value(3))
                .andExpect(jsonPath("$.clicksByDay", hasSize(7)))
                .andExpect(jsonPath("$.clicksByDay[5].date").value("2026-01-15"))
                .andExpect(jsonPath("$.clicksByDay[5].clicks").value(2))
                .andExpect(jsonPath("$.clicksByDay[6].clicks").value(2))
                .andExpect(jsonPath("$.topReferrers[0].host").value("news.example.com"))
                .andExpect(jsonPath("$.topReferrers[0].clicks").value(2))
                .andExpect(jsonPath("$.topReferrers", hasSize(2)))
                .andExpect(jsonPath("$.lastClickAt").value("2026-01-16T10:00:00Z"));

        mvc.perform(get("/api/v1/links/" + code + "/stats").param("days", "0")).andExpect(status().isBadRequest());
        mvc.perform(get("/api/v1/links/" + code + "/stats").param("days", "abc")).andExpect(status().isBadRequest());
    }

    @Test
    void exposesHealthAndOpenApi() throws Exception {
        mvc.perform(get("/actuator/health")).andExpect(status().isOk()).andExpect(jsonPath("$.status").value("UP"));
        mvc.perform(get("/v3/api-docs")).andExpect(status().isOk())
                .andExpect(jsonPath("$.paths['/api/v1/links']").exists());
    }

    private ResultActions create(String content) throws Exception {
        return mvc.perform(post("/api/v1/links").contentType(MediaType.APPLICATION_JSON).content(content));
    }

    private void click(String code, String ip, String referrer) throws Exception {
        mvc.perform(get("/" + code).with(r -> {
            r.setRemoteAddr(ip);
            if (referrer != null) {
                r.addHeader("Referer", referrer);
            }
            r.addHeader("User-Agent", "JUnit");
            return r;
        })).andExpect(status().isFound());
    }

    private JsonNode body(ResultActions result) throws Exception {
        return json.readTree(result.andReturn().getResponse().getContentAsString());
    }
}
