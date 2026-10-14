"""LLM client abstraction.

The pipeline talks to a small protocol (``LLMClient.complete``) in the Anthropic message shape:
``messages`` are ``{"role", "content"}`` dicts whose content is a string or a list of ``text`` /
``tool_use`` / ``tool_result`` blocks.  ``AnthropicLLM`` is the production implementation on the
official ``anthropic`` SDK; ``FakeLLM`` is a deterministic, scripted stand-in used by the tests and
by the offline evaluation of the pipeline.  Neither is ever allowed to touch the database.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("ulis.assistant.llm")

# Opt-in beta that re-runs a safety-refused request on a fallback model inside the same call. Models that
# do not accept it reject the request; the client then drops it for good (see ``AnthropicLLM.complete``).
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_input_tokens += other.cached_input_tokens


@dataclass
class LLMRequest:
    stage: str  # understanding | searching | drafting (informational; used for effort and tests)
    system: str
    messages: list[dict[str, Any]]
    max_tokens: int
    tools: list[dict[str, Any]] = field(default_factory=list)
    # JSON schema the reply must satisfy (structured output); None for free text / tool use.
    json_schema: dict[str, Any] | None = None
    effort: str | None = None
    # Mark the (growing) conversation prefix as cacheable; only worth it for multi-turn tool loops.
    cache: bool = False


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    # end_turn | tool_use | max_tokens | refusal | ...
    stop_reason: str
    usage: Usage
    # Provider-native assistant content, echoed back verbatim as the next assistant turn.
    content: list[Any]
    model: str


class LLMError(RuntimeError):
    """A model call failed. ``message`` is safe to show to the user (never contains request text)."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class LLMClient(Protocol):
    model: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


# --------------------------------------------------------------------------- Anthropic


class AnthropicLLM:
    """Messages API client with prompt caching, strict tools, structured output and refusal fallback."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        fallbacks: str = "default",
        send_effort: bool = True,
        timeout: float = 600.0,
        client: Any = None,
    ) -> None:
        import anthropic

        self._anthropic = anthropic
        self.model = model
        self._use_fallbacks = fallbacks != "off"
        self._send_effort = send_effort
        # The SDK retries connection errors, 408, 409, 429 and 5xx with backoff.
        self._client = client or anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=3)

    def complete(self, request: LLMRequest) -> LLMResponse:
        use_format = request.json_schema is not None
        for attempt in range(1, 4):
            try:
                return self._call(request, use_format)
            except self._anthropic.BadRequestError as exc:
                text = _message(exc)
                # An optional feature this model does not accept: drop it and retry (at most twice).
                if self._use_fallbacks and "fallback" in text and attempt < 3:
                    log.warning("refusal fallback rejected (request %s); disabling it", _request_id(exc))
                    self._use_fallbacks = False
                elif use_format and _mentions_format(text) and attempt < 3:
                    log.warning("structured output rejected (request %s); retrying without it", _request_id(exc))
                    use_format = False
                else:
                    raise self._map_error(exc) from exc
            except self._anthropic.APIError as exc:
                raise self._map_error(exc) from exc
        raise LLMError("llm_bad_request", "The language-model provider rejected the request.")  # pragma: no cover

    def _call(self, request: LLMRequest, use_format: bool) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": request.messages,
        }
        if request.cache:
            kwargs["cache_control"] = {"type": "ephemeral"}
        if request.tools:
            kwargs["tools"] = request.tools
            # Forced tool choice is rejected by current models: steer from the prompt instead.
            kwargs["tool_choice"] = {"type": "auto"}
        output_config: dict[str, Any] = {}
        if self._send_effort and request.effort:
            output_config["effort"] = request.effort
        if use_format and request.json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.json_schema}
        if output_config:
            kwargs["output_config"] = output_config
        stream: Any
        if self._use_fallbacks:
            stream = self._client.beta.messages.stream(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
        else:
            stream = self._client.messages.stream(**kwargs)
        with stream as events:
            message: Any = events.get_final_message()
        text = "".join(b.text for b in message.content if b.type == "text")
        calls = [
            ToolCall(b.id, b.name, dict(b.input) if isinstance(b.input, dict) else {})
            for b in message.content
            if b.type == "tool_use"
        ]
        u = message.usage
        cached = getattr(u, "cache_read_input_tokens", 0) or 0
        created = getattr(u, "cache_creation_input_tokens", 0) or 0
        return LLMResponse(
            text=text,
            tool_calls=calls,
            stop_reason=message.stop_reason or "end_turn",
            usage=Usage(u.input_tokens + cached + created, u.output_tokens, cached),
            content=list(message.content),
            model=message.model,
        )

    def _map_error(self, exc: Exception) -> LLMError:
        a = self._anthropic
        rid = _request_id(exc)
        suffix = f" (provider request {rid})" if rid else ""
        log.warning(
            "model call failed: %s status=%s request=%s", type(exc).__name__, getattr(exc, "status_code", "-"), rid
        )
        if isinstance(exc, a.AuthenticationError | a.PermissionDeniedError):
            return LLMError("llm_auth", "The language-model provider rejected the configured API key." + suffix)
        if isinstance(exc, a.NotFoundError):
            return LLMError("llm_model_not_found", f"The configured model {self.model!r} was not found." + suffix)
        if isinstance(exc, a.RateLimitError):
            return LLMError(
                "llm_rate_limited", "The language-model provider is rate limiting requests." + suffix, retryable=True
            )
        if isinstance(exc, a.APITimeoutError):
            return LLMError("llm_timeout", "The language-model request timed out." + suffix, retryable=True)
        if isinstance(exc, a.APIConnectionError):
            return LLMError(
                "llm_unreachable", "The language-model provider could not be reached." + suffix, retryable=True
            )
        if isinstance(exc, a.BadRequestError):
            return LLMError("llm_bad_request", "The language-model provider rejected the request." + suffix)
        status = getattr(exc, "status_code", 0) or 0
        if status >= 500:
            return LLMError(
                "llm_unavailable", "The language-model provider is temporarily unavailable." + suffix, retryable=True
            )
        return LLMError("llm_error", "The language-model call failed." + suffix)


def _request_id(exc: Exception) -> str | None:
    rid = getattr(exc, "request_id", None)
    return rid if isinstance(rid, str) else None


def _message(exc: Exception) -> str:
    return str(getattr(exc, "message", "") or exc).lower()


def _mentions_format(text: str) -> bool:
    return any(word in text for word in ("output_config", "schema", "format", "structured"))


# --------------------------------------------------------------------------- Fake (tests / offline)

Step = Any  # LLMResponse | dict | list | str | Callable[[LLMRequest], Step]


def text_response(
    payload: Any, *, stop_reason: str = "end_turn", model: str = "fake-model", usage: Usage | None = None
) -> LLMResponse:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return LLMResponse(
        text=text,
        tool_calls=[],
        stop_reason=stop_reason,
        usage=usage or Usage(100, 50),
        content=[{"type": "text", "text": text}],
        model=model,
    )


def tool_use_response(
    *calls: tuple[str, dict[str, Any]], text: str = "", model: str = "fake-model", start: int = 1
) -> LLMResponse:
    tool_calls = [ToolCall(f"toolu_fake_{start + i}", name, args) for i, (name, args) in enumerate(calls)]
    content: list[Any] = [{"type": "text", "text": text}] if text else []
    content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in tool_calls]
    return LLMResponse(text, tool_calls, "tool_use", Usage(100, 50), content, model)


class FakeLLM:
    """Deterministic scripted client.

    ``script`` is consumed one step per call.  A step is an ``LLMResponse``; a dict/list (sent as JSON
    text); a string; or a callable ``(LLMRequest) -> step`` for replies that depend on what the
    pipeline sent (for example the authority ids it retrieved).  Every request is recorded, with
    messages deep-copied at call time, so tests can assert on exactly what the model was shown.
    """

    def __init__(self, script: Iterable[Step] = (), *, model: str = "fake-model") -> None:
        self.model = model
        self._script = list(script)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(dataclasses.replace(request, messages=copy.deepcopy(request.messages)))
        if not self._script:
            raise LLMError("fake_script_exhausted", f"FakeLLM has no scripted reply for call #{len(self.requests)}")
        step: Any = self._script.pop(0)
        while callable(step):
            step = step(request)
        if isinstance(step, LLMResponse):
            return step
        return text_response(step, model=self.model)

    @property
    def remaining(self) -> int:
        return len(self._script)
