"""LLM client wrapper (request construction, error mapping), configuration and FakeLLM, without any network."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from ulis.assistant.config import DEFAULT_MODEL, DEFAULT_MODEL_DEEP, load_assistant_config
from ulis.assistant.jobs import default_llm_factory
from ulis.assistant.llm import (
    FALLBACK_BETA,
    AnthropicLLM,
    FakeLLM,
    LLMError,
    LLMRequest,
    text_response,
    tool_use_response,
)

REQUEST = httpx2.Request("POST", "https://api.invalid/v1/messages")


class _Stream:
    def __init__(self, message: Any) -> None:
        self._message = message

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get_final_message(self) -> Any:
        return self._message


class _Endpoint:
    def __init__(self, owner: StubClient, name: str) -> None:
        self._owner, self._name = owner, name

    def stream(self, **kwargs: Any) -> _Stream:
        self._owner.calls.append((self._name, kwargs))
        if self._owner.errors:
            raise self._owner.errors.pop(0)
        return _Stream(self._owner.message)


class StubClient:
    def __init__(self, message: Any = None, errors: list[Exception] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.errors = errors or []
        self.message = message or message_with("ok")
        self.messages = _Endpoint(self, "messages")
        self.beta = SimpleNamespace(messages=_Endpoint(self, "beta"))


def message_with(text: str, tool: tuple[str, str, dict[str, Any]] | None = None, stop: str = "end_turn") -> Any:
    content = [SimpleNamespace(type="text", text=text)]
    if tool:
        content.append(SimpleNamespace(type="tool_use", id=tool[0], name=tool[1], input=tool[2]))
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=3, cache_creation_input_tokens=2)
    return SimpleNamespace(content=content, stop_reason=stop, usage=usage, model="test-model")


def request(**kw: Any) -> LLMRequest:
    base: dict[str, Any] = {
        "stage": "searching",
        "system": "system prompt",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 1234,
    }
    return LLMRequest(**{**base, **kw})


def status_error(cls: type[anthropic.APIStatusError], status: int, message: str = "bad") -> anthropic.APIStatusError:
    return cls(message, response=httpx2.Response(status, request=REQUEST), body=None)


def test_request_is_built_with_strict_tools_caching_structured_output_and_fallback() -> None:
    stub = StubClient(message_with("{}", ("toolu_1", "search_law", {"query": "q"}), "tool_use"))
    llm = AnthropicLLM("sk-test", "test-model", client=stub)
    tools = [{"name": "t", "description": "d", "strict": True, "input_schema": {"type": "object"}}]
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    response = llm.complete(request(tools=tools, json_schema=schema, effort="medium", cache=True))

    ((endpoint, kwargs),) = stub.calls
    assert endpoint == "beta" and kwargs["betas"] == [FALLBACK_BETA] and kwargs["fallbacks"] == "default"
    assert kwargs["model"] == "test-model" and kwargs["max_tokens"] == 1234
    assert kwargs["system"] == "system prompt" and kwargs["cache_control"] == {"type": "ephemeral"}
    assert kwargs["tools"] == tools and kwargs["tool_choice"] == {"type": "auto"}  # forced tool use is never requested
    assert kwargs["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": schema}}
    assert "thinking" not in kwargs and "temperature" not in kwargs
    assert [c.name for c in response.tool_calls] == ["search_law"] and response.tool_calls[0].input == {"query": "q"}
    assert response.stop_reason == "tool_use" and response.text == "{}"
    assert (response.usage.input_tokens, response.usage.output_tokens, response.usage.cached_input_tokens) == (15, 5, 3)
    assert len(response.content) == 2


def test_a_model_that_rejects_the_refusal_fallback_is_called_without_it_from_then_on() -> None:
    stub = StubClient(
        errors=[status_error(anthropic.BadRequestError, 400, "fallbacks: Extra inputs are not permitted")]
    )
    llm = AnthropicLLM("k", "other-model", client=stub)
    llm.complete(request())
    llm.complete(request())
    assert [endpoint for endpoint, _ in stub.calls] == ["beta", "messages", "messages"]
    ((_, kwargs), *_) = stub.calls[1:]
    assert set(kwargs) == {"model", "max_tokens", "system", "messages"}  # no cache, tools, effort or schema asked for


def test_fallback_and_effort_can_be_switched_off() -> None:
    off = StubClient()
    AnthropicLLM("k", "test-model-deep", fallbacks="off", send_effort=False, client=off).complete(
        request(effort="high", cache=True)
    )
    ((endpoint, kwargs),) = off.calls
    assert endpoint == "messages" and "output_config" not in kwargs and kwargs["cache_control"] == {"type": "ephemeral"}


def test_rejected_structured_output_is_retried_once_in_prose_mode() -> None:
    stub = StubClient(errors=[status_error(anthropic.BadRequestError, 400, "output_config.format: unsupported schema")])
    llm = AnthropicLLM("k", "test-model", client=stub)
    llm.complete(request(json_schema={"type": "object"}, effort="low"))
    assert len(stub.calls) == 2
    assert "format" in stub.calls[0][1]["output_config"] and stub.calls[1][1]["output_config"] == {"effort": "low"}
    unrelated = StubClient(errors=[status_error(anthropic.BadRequestError, 400, "prompt is too long")])
    with pytest.raises(LLMError) as err:
        AnthropicLLM("k", "test-model", client=unrelated).complete(request())
    assert err.value.code == "llm_bad_request" and len(unrelated.calls) == 1


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (status_error(anthropic.AuthenticationError, 401, "invalid x-api-key sk-secret-123"), "llm_auth", False),
        (status_error(anthropic.PermissionDeniedError, 403), "llm_auth", False),
        (status_error(anthropic.NotFoundError, 404), "llm_model_not_found", False),
        (status_error(anthropic.RateLimitError, 429), "llm_rate_limited", True),
        (status_error(anthropic.InternalServerError, 529), "llm_unavailable", True),
        (anthropic.APIConnectionError(request=REQUEST), "llm_unreachable", True),
        (anthropic.APITimeoutError(request=REQUEST), "llm_timeout", True),
    ],
)
def test_provider_errors_map_to_safe_codes(error: Exception, code: str, retryable: bool) -> None:
    llm = AnthropicLLM("sk-secret-123", "test-model", client=StubClient(errors=[error]))
    with pytest.raises(LLMError) as err:
        llm.complete(request())
    assert err.value.code == code and err.value.retryable is retryable
    assert "sk-secret" not in err.value.message  # provider messages (which may echo keys or prompts) are not forwarded


def test_real_sdk_client_can_be_constructed_offline() -> None:
    llm = AnthropicLLM("sk-test-offline", "test-model-deep", timeout=5.0)
    assert llm.model == "test-model-deep"


def test_configuration_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ANTHROPIC_API_KEY",
        "ULIS_LLM_MODEL",
        "ULIS_LLM_MODEL_DEEP",
        "ULIS_RESEARCH_MAX_SEARCHES",
        "ULIS_RESEARCH_MAX_SEARCHES_DEEP",
        "ULIS_RESEARCH_MAX_OUTPUT_TOKENS",
        "ULIS_RESEARCH_TTL_HOURS",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = load_assistant_config()
    assert not cfg.llm_configured and default_llm_factory(cfg) is None
    assert (cfg.model, cfg.model_deep) == (DEFAULT_MODEL, DEFAULT_MODEL_DEEP)
    assert (cfg.search_cap("standard"), cfg.search_cap("deep"), cfg.ttl_hours) == (12, 30, 24.0)
    assert cfg.output_budget("deep") == 2 * cfg.output_budget("standard")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("ULIS_LLM_MODEL", "my-model")
    monkeypatch.setenv("ULIS_LLM_MODEL_DEEP", "my-deep-model")
    monkeypatch.setenv("ULIS_RESEARCH_MAX_SEARCHES", "40")
    monkeypatch.setenv("ULIS_RESEARCH_TTL_HOURS", "1.5")
    monkeypatch.setenv("ULIS_RESEARCH_MAX_OUTPUT_TOKENS", "100")
    cfg = load_assistant_config()
    assert cfg.llm_configured and cfg.model_for("standard") == "my-model" and cfg.model_for("deep") == "my-deep-model"
    assert cfg.search_cap("standard") == 40 and cfg.search_cap("deep") == 40  # deep is never below standard
    assert cfg.ttl_hours == 1.5 and cfg.max_output_tokens == 4000  # floor keeps the pipeline workable
    monkeypatch.setenv("ULIS_RESEARCH_MAX_SEARCHES", "many")
    with pytest.raises(ValueError, match="ULIS_RESEARCH_MAX_SEARCHES"):
        load_assistant_config()
    factory = default_llm_factory(cfg.__class__(api_key="sk-x"))
    assert factory is not None and factory("deep").model == DEFAULT_MODEL_DEEP


def test_fake_llm_is_scripted_and_records_what_it_was_shown() -> None:
    fake = FakeLLM(
        [
            {"a": 1},
            "plain text",
            lambda req: text_response(f"saw {req.messages[0]['content']}"),
            tool_use_response(("coverage", {"jurisdiction": "XA"})),
        ]
    )
    messages = [{"role": "user", "content": "hi"}]
    first = fake.complete(request(messages=messages))
    messages.append({"role": "assistant", "content": "later mutation"})
    assert first.text == '{"a": 1}' and first.stop_reason == "end_turn"
    assert fake.complete(request()).text == "plain text"
    assert fake.complete(request()).text == "saw hello"
    called = fake.complete(request())
    assert called.stop_reason == "tool_use" and called.tool_calls[0].name == "coverage"
    assert len(fake.requests[0].messages) == 1  # recorded as at call time
    with pytest.raises(LLMError, match="no scripted reply"):
        fake.complete(request())
