"""ClaudeChatModel against a stubbed Anthropic client: request shape, refusal and error classification."""

from types import SimpleNamespace

import anthropic
import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from agentic_sdlc.llm import ClaudeChatModel
from agentic_sdlc.resilience import OutputValidationError, PermanentError, TransientError


class StubStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.response


def install(monkeypatch, response=None, error=None):
    calls = []

    class StubClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))
            self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

        def stream(self, **kwargs):
            calls.append(("stream", kwargs))
            if error:
                raise error
            return StubStream(response)

    monkeypatch.setattr(anthropic, "Anthropic", StubClient)
    return calls


def message(stop_reason="end_turn", text='{"ok": true}'):
    return SimpleNamespace(stop_reason=stop_reason, model="claude-opus-5-5", stop_details=None,
                           content=[SimpleNamespace(type="thinking", thinking=""),
                                    SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5))


MESSAGES = [SystemMessage(content="sys"), HumanMessage(content="hi")]


def test_request_shape_and_text_extraction(monkeypatch):
    calls = install(monkeypatch, response=message())
    out = ClaudeChatModel().invoke(MESSAGES)
    assert out.content == '{"ok": true}'
    init, (_, req) = calls[0], calls[1]
    assert init[1] == {"max_retries": 0}  # our retry layer is the only one
    assert req["model"] == "claude-opus-5-5"
    assert req["system"] == "sys" and req["messages"] == [{"role": "user", "content": "hi"}]
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert req["output_config"] == {"effort": "high"}
    assert "thinking" not in req and "temperature" not in req


def test_refusal_is_permanent(monkeypatch):
    install(monkeypatch, response=message(stop_reason="refusal", text=""))
    with pytest.raises(PermanentError, match="declined"):
        ClaudeChatModel().invoke(MESSAGES)


def test_truncation_is_an_output_error(monkeypatch):
    install(monkeypatch, response=message(stop_reason="max_tokens"))
    with pytest.raises(OutputValidationError):
        ClaudeChatModel().invoke(MESSAGES)


def _status_error(cls, code):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("err", response=httpx.Response(code, request=request), body=None)


def test_error_classification(monkeypatch):
    install(monkeypatch, error=_status_error(anthropic.RateLimitError, 429))
    with pytest.raises(TransientError):
        ClaudeChatModel().invoke(MESSAGES)
    install(monkeypatch, error=_status_error(anthropic.BadRequestError, 400))
    with pytest.raises(PermanentError):
        ClaudeChatModel().invoke(MESSAGES)
    install(monkeypatch, error=_status_error(anthropic.InternalServerError, 503))
    with pytest.raises(TransientError):
        ClaudeChatModel().invoke(MESSAGES)


def test_provider_chain_selection(monkeypatch, settings):
    from agentic_sdlc.runtime import build_runtime
    from agentic_sdlc.scenario import load_scenario

    scenario = load_scenario("greenfield")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    settings.llm_mode = "auto"
    assert build_runtime("a", settings, scenario).llm.provider_names == ["replay"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert build_runtime("b", settings, scenario).llm.provider_names == ["claude", "replay"]
    settings.llm_mode = "offline"
    assert build_runtime("c", settings, scenario).llm.provider_names == ["replay"]
