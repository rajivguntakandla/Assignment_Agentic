package io.agentic.shortener.web;

import io.agentic.shortener.config.ShortenerProperties;
import io.agentic.shortener.service.AnalyticsService;
import io.agentic.shortener.service.CreateLinkCommand;
import io.agentic.shortener.service.CreateLinkResult;
import io.agentic.shortener.service.LinkService;
import io.agentic.shortener.web.dto.CreateLinkRequest;
import io.agentic.shortener.web.dto.LinkResponse;
import io.agentic.shortener.web.dto.LinkStatsResponse;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Size;
import java.net.URI;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@Validated
@RestController
@RequestMapping("/api/v1/links")
@Tag(name = "Links", description = "Create, inspect and retire short links")
public class LinkController {

    private final LinkService linkService;
    private final AnalyticsService analyticsService;
    private final ShortenerProperties properties;

    public LinkController(LinkService linkService, AnalyticsService analyticsService, ShortenerProperties properties) {
        this.linkService = linkService;
        this.analyticsService = analyticsService;
        this.properties = properties;
    }

    @PostMapping
    @Operation(summary = "Shorten a URL",
            description = "Returns 201 on creation; 200 when an Idempotency-Key replay matches the original request.")
    public ResponseEntity<LinkResponse> create(
            @Valid @RequestBody CreateLinkRequest request,
            @RequestHeader(name = "Idempotency-Key", required = false) @Size(min = 1, max = 128) String idempotencyKey) {
        CreateLinkResult result = linkService.create(
                new CreateLinkCommand(request.url(), request.customAlias(), request.expiresAt(), idempotencyKey));
        LinkResponse body = LinkResponse.from(result.link(), properties.baseUrl());
        if (!result.created()) {
            return ResponseEntity.ok(body);
        }
        return ResponseEntity.created(URI.create("/api/v1/links/" + body.code())).body(body);
    }

    @GetMapping("/{code}")
    @Operation(summary = "Get link metadata")
    public LinkResponse get(@PathVariable String code) {
        return LinkResponse.from(linkService.get(code), properties.baseUrl());
    }

    @GetMapping("/{code}/stats")
    @Operation(summary = "Click analytics: totals, unique visitors, daily series (UTC), top referrers")
    public LinkStatsResponse stats(@PathVariable String code, @RequestParam(defaultValue = "30") int days) {
        return analyticsService.stats(code, days);
    }

    @DeleteMapping("/{code}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    @Operation(summary = "Deactivate a link (soft delete; redirects return 410 afterwards)")
    public void deactivate(@PathVariable String code) {
        linkService.deactivate(code);
    }
}
