package io.agentic.shortener.web;

import io.agentic.shortener.service.LinkService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletRequest;
import java.net.URI;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

@RestController
@Tag(name = "Redirect")
public class RedirectController {

    private final LinkService linkService;

    public RedirectController(LinkService linkService) {
        this.linkService = linkService;
    }

    /**
     * 302 (not 301) plus {@code no-store}: browsers must come back to us on every click, otherwise analytics
     * undercount and deactivation/expiry could not take effect for visitors who cached a permanent redirect.
     */
    @GetMapping("/{code:[A-Za-z0-9_-]{3,32}}")
    @Operation(summary = "Follow a short link")
    public ResponseEntity<Void> redirect(@PathVariable String code, HttpServletRequest request) {
        String target = linkService.resolveForRedirect(code, request.getHeader(HttpHeaders.REFERER),
                request.getRemoteAddr(), request.getHeader(HttpHeaders.USER_AGENT));
        return ResponseEntity.status(HttpStatus.FOUND)
                .location(URI.create(target))
                .cacheControl(CacheControl.noStore())
                .build();
    }
}
