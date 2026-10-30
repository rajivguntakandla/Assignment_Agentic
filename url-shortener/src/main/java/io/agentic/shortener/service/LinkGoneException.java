package io.agentic.shortener.service;

/** The link existed but can no longer be followed (expired or deactivated). Maps to HTTP 410. */
public class LinkGoneException extends RuntimeException {

    public LinkGoneException(String code, String reason) {
        super("Link '" + code + "' is no longer available: " + reason);
    }
}
