import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel

from agentic_sdlc.audit import AuditLog
from agentic_sdlc.llm import LLMRouter, ReplayChatModel, extract_json
from agentic_sdlc.resilience import BudgetExceeded, FaultInjector, OutputValidationError, PermanentError
from agentic_sdlc.safety import BudgetGuard
from agentic_sdlc.settings import Budget


class Out(BaseModel):
    value: int


class Scripted(BaseChatModel):
    replies: list = []
    seen: list = []

    @property
    def _llm_type(self):
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append([m.content for m in messages])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=reply))])


def router(tmp_path, providers, faults=None, max_calls=50):
    audit = AuditLog(tmp_path / "audit.jsonl", "r")
    return LLMRouter(providers, audit, BudgetGuard(Budget(max_llm_calls=max_calls)), FaultInjector(faults or {}),
                     max_retries=2, backoff_base_s=0, sleep=lambda _s: None), audit


def call(r, **kw):
    return r.structured(agent="planning", stage="planning", variant=1, system="s", user="u", schema=Out, **kw)


def test_extract_json_handles_fences_and_prose():
    assert extract_json('Here:\n```json\n{"value": 1}\n```') == '{"value": 1}'
    assert extract_json('noise {"value": 2} trailing') == '{"value": 2}'
    with pytest.raises(OutputValidationError):
        extract_json("no json")


def test_transient_errors_are_retried_with_backoff(tmp_path):
    r, audit = router(tmp_path, [("p", Scripted(replies=['{"value": 3}']))], {"llm_transient:planning": 2})
    assert call(r) == (Out(value=3), "p")
    events = [e["event"] for e in audit.events()]
    assert events.count("retry") == 2 and events.count("llm_call") == 3


def test_invalid_output_gets_corrective_feedback(tmp_path):
    model = Scripted(replies=['{"value": "not-int"}', '{"value": 4}'], seen=[])
    r, _ = router(tmp_path, [("p", model)])
    assert call(r)[0].value == 4
    assert "rejected" in model.seen[1][-1]


def test_semantic_validation_failure_is_retried(tmp_path):
    r, _ = router(tmp_path, [("p", Scripted(replies=['{"value": -1}', '{"value": 5}']))])

    def positive(o):
        if o.value < 0:
            raise OutputValidationError("value must be positive")

    assert call(r, validate=positive)[0].value == 5


def test_falls_back_to_next_provider(tmp_path):
    r, audit = router(tmp_path, [("primary", Scripted(replies=[PermanentError("down")])),
                                 ("secondary", Scripted(replies=['{"value": 6}']))])
    assert call(r) == (Out(value=6), "secondary")
    assert any(e["event"] == "fallback" for e in audit.events())


def test_all_providers_failing_raises(tmp_path):
    r, _ = router(tmp_path, [("p", Scripted(replies=[PermanentError("down")]))])
    with pytest.raises(PermanentError):
        call(r)


def test_budget_is_enforced(tmp_path):
    r, _ = router(tmp_path, [("p", Scripted(replies=['{"value": 1}'] * 5))], max_calls=1)
    call(r)
    with pytest.raises(BudgetExceeded):
        call(r)


def test_replay_model_selects_variant(tmp_path):
    replay = ReplayChatModel(recordings={"planning": [{"value": 1}, {"value": 2}]})
    r, _ = router(tmp_path, [("replay", replay)])
    assert r.structured(agent="planning", stage="planning", variant=2, system="s", user="u", schema=Out)[0].value == 2
    assert r.structured(agent="planning", stage="planning", variant=9, system="s", user="u", schema=Out)[0].value == 2
