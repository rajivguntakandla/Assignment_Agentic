"""LLM integration.

* :class:`ClaudeChatModel` - a LangChain chat model that calls Claude through the official Anthropic SDK, with
  server-side refusal fallbacks enabled. SDK-level retries are disabled so that *our* bounded, audited retry
  policy is the only one in effect.
* :class:`ReplayChatModel` - a deterministic LangChain chat model that replays responses recorded for a
  scenario. It makes runs reproducible without network access or API keys (CI, demos, tests).
* :class:`LLMRouter` - provider chain with bounded retries, output validation with corrective feedback, provider
  fallback, budget accounting and audit of every call.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from typing import Any, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ValidationError

from .audit import AuditLog, fingerprint
from .resilience import FaultInjector, OutputValidationError, PermanentError, TransientError, retry_call
from .safety import BudgetGuard

M = TypeVar("M", bound=BaseModel)
_REQUEST_ID = re.compile(r"Request-ID: agent=(?P<agent>[\w-]+); variant=(?P<variant>\d+)")


class ClaudeChatModel(BaseChatModel):
    model: str = "claude-opus-5-5"
    effort: str = "high"
    max_tokens: int = 32000

    @property
    def _llm_type(self) -> str:
        return "anthropic-claude"

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None,
                  **kwargs: Any) -> ChatResult:
        import anthropic

        system = "\n\n".join(str(m.content) for m in messages if isinstance(m, SystemMessage))
        convo = [{"role": "assistant" if isinstance(m, AIMessage) else "user", "content": str(m.content)}
                 for m in messages if not isinstance(m, SystemMessage)]
        client = anthropic.Anthropic(max_retries=0)
        try:
            # Streaming: implementation outputs can be long; avoids HTTP timeouts on large max_tokens.
            with client.beta.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=convo,
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                response = stream.get_final_message()
                request_id = getattr(stream, "request_id", None)
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as exc:
            raise TransientError(f"claude transient error: {type(exc).__name__}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code == 529:
                raise TransientError(f"claude server error {exc.status_code}") from exc
            raise PermanentError(f"claude request rejected ({exc.status_code})") from exc
        if response.stop_reason == "refusal":
            category = getattr(getattr(response, "stop_details", None), "category", None)
            raise PermanentError(f"claude declined the request (category={category})")
        if response.stop_reason == "max_tokens":
            raise OutputValidationError("response truncated at max_tokens")
        text = "".join(block.text for block in response.content if block.type == "text")
        meta = {"model": response.model, "request_id": request_id,
                "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text, response_metadata=meta))])


class ReplayChatModel(BaseChatModel):
    """Replays recorded responses keyed by the ``Request-ID`` line every agent prompt carries."""

    recordings: dict[str, list[Any]] = {}

    @property
    def _llm_type(self) -> str:
        return "replay"

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None,
                  **kwargs: Any) -> ChatResult:
        prompt = next((str(m.content) for m in messages if isinstance(m, HumanMessage)), "")
        m = _REQUEST_ID.search(prompt)
        if not m:
            raise PermanentError("replay: prompt carries no Request-ID")
        responses = self.recordings.get(m.group("agent"))
        if not responses:
            raise PermanentError(f"replay: no recording for agent {m.group('agent')!r}")
        chosen = responses[min(int(m.group("variant")), len(responses)) - 1]
        text = chosen if isinstance(chosen, str) else json.dumps(chosen)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text,
                                                                         response_metadata={"model": "replay"}))])


def claude_credentials_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def extract_json(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise OutputValidationError("no JSON object found in model output")
    return text[start:end + 1]


class LLMRouter:
    def __init__(self, providers: list[tuple[str, BaseChatModel]], audit: AuditLog, budget: BudgetGuard,
                 faults: FaultInjector, max_retries: int, backoff_base_s: float,
                 sleep: Callable[[float], None] = time.sleep):
        self.providers = providers
        self.audit = audit
        self.budget = budget
        self.faults = faults
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.sleep = sleep

    @property
    def provider_names(self) -> list[str]:
        return [name for name, _ in self.providers]

    def structured(self, *, agent: str, stage: str, variant: int, system: str, user: str, schema: type[M],
                   validate: Callable[[M], None] | None = None, span_id: str | None = None) -> tuple[M, str]:
        """Return a schema-valid object from the first provider able to produce one."""
        last_error: Exception | None = None
        for idx, (name, model) in enumerate(self.providers):
            try:
                return self._from_provider(name, model, idx == 0, agent, stage, variant, system, user, schema,
                                           validate, span_id), name
            except (TransientError, PermanentError, OutputValidationError) as exc:
                last_error = exc
                if idx + 1 < len(self.providers):
                    self.audit.record("fallback", stage=stage, parent_span_id=span_id, data={
                        "scope": "llm_provider", "agent": agent, "from": name, "to": self.providers[idx + 1][0],
                        "reason": str(exc)[:300]})
        raise PermanentError(f"all LLM providers failed for {agent}: {last_error}")

    def _from_provider(self, name: str, model: BaseChatModel, primary: bool, agent: str, stage: str, variant: int,
                       system: str, user: str, schema: type[M], validate: Callable[[M], None] | None,
                       span_id: str | None) -> M:
        header = f"Request-ID: agent={agent}; variant={variant}\n\n"
        prompt = ChatPromptTemplate.from_messages([("system", "{system}"), ("human", "{user}")])
        messages: list[BaseMessage] = prompt.invoke({"system": system, "user": header + user}).to_messages()

        def call(attempt: int) -> M:
            self.budget.charge_llm_call()
            started = time.monotonic()
            ok, error, text, meta = False, None, "", {}
            try:
                if primary and self.faults.take(f"llm_transient:{agent}"):
                    raise TransientError("injected transient fault (simulated 529 overloaded)")
                if primary and self.faults.take(f"llm_down:{agent}"):
                    raise PermanentError("injected provider outage")
                reply = model.invoke(messages)
                text, meta = str(reply.content), dict(reply.response_metadata or {})
                if primary and self.faults.take(f"invalid_output:{agent}"):
                    text = "Sure! Here is what you asked for (no JSON, oops)."
                parsed = schema.model_validate_json(extract_json(text))
                if validate:
                    validate(parsed)
                ok = True
                return parsed
            except ValidationError as exc:
                error = f"schema validation failed: {exc.errors()[:3]}"
                raise OutputValidationError(error) from exc
            except (TransientError, PermanentError, OutputValidationError) as exc:
                error = str(exc)
                raise
            finally:
                self.audit.record("llm_call", actor=f"agent:{agent}", stage=stage, parent_span_id=span_id, data={
                    "provider": name, "attempt": attempt, "ok": ok, "error": error,
                    "latency_s": round(time.monotonic() - started, 3), "prompt_fp": fingerprint(messages[-1].content),
                    "response_fp": fingerprint(text) if text else None,
                    **{k: meta[k] for k in ("model", "request_id", "input_tokens", "output_tokens") if k in meta}})

        def on_retry(attempt: int, exc: BaseException, delay: float) -> None:
            self.audit.record("retry", stage=stage, parent_span_id=span_id, data={
                "scope": "llm", "agent": agent, "provider": name, "attempt": attempt, "reason": str(exc)[:300],
                "backoff_s": round(delay, 3)})
            if isinstance(exc, OutputValidationError):
                # Corrective feedback: show the model its own error, ask for a fixed answer.
                messages.append(HumanMessage(content=(
                    f"{header}Your previous answer was rejected: {exc}. Return ONLY one corrected JSON object "
                    f"that satisfies the schema and the constraints.")))

        return retry_call(call, max_retries=self.max_retries, backoff_base_s=self.backoff_base_s,
                          retry_on=(TransientError, OutputValidationError), on_retry=on_retry, sleep=self.sleep)


def schema_instructions(schema: type[BaseModel]) -> str:
    return ("Respond with exactly one JSON object (no prose, no markdown fences) that validates against this "
            f"JSON Schema:\n{json.dumps(schema.model_json_schema(), separators=(',', ':'))}")
