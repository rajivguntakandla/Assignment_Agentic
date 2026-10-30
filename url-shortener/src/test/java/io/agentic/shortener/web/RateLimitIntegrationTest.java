package io.agentic.shortener.web;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest(properties = {
        "shortener.rate-limit.enabled=true",
        "shortener.rate-limit.capacity=2",
        "shortener.rate-limit.refill-per-minute=1"})
@AutoConfigureMockMvc
@ActiveProfiles("test")
class RateLimitIntegrationTest {

    @Autowired
    MockMvc mvc;

    @Test
    void throttlesCreationButNotReads() throws Exception {
        for (int i = 0; i < 2; i++) {
            mvc.perform(post("/api/v1/links").contentType(MediaType.APPLICATION_JSON)
                    .content("{\"url\":\"https://example.com/" + i + "\"}")).andExpect(status().isCreated());
        }
        mvc.perform(post("/api/v1/links").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"url\":\"https://example.com/x\"}"))
                .andExpect(status().isTooManyRequests())
                .andExpect(header().exists("Retry-After"))
                .andExpect(jsonPath("$.status").value(429));
        mvc.perform(get("/api/v1/links/unknown1")).andExpect(status().isNotFound());
    }
}
