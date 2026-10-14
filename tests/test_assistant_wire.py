"""The real Anthropic SDK against a canned server: serialisation, headers, streaming and block replay."""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2

from ulis.assistant.llm import FALLBACK_BETA, AnthropicLLM, LLMRequest

TOOL = {
    "name": "coverage",
    "description": "d",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"jurisdiction": {"type": "string"}},
        "required": ["jurisdiction"],
        "additionalProperties": False,
    },
}
SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def sse(*events: tuple[str, dict[str, Any]]) -> bytes:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


def start(message_id: str, usage: dict[str, int]) -> tuple[str, dict[str, Any]]:
    message = {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": "test-model",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": usage,
    }
    return "message_start", {"type": "message_start", "message": message}


def finish(stop_reason: str, output_tokens: int) -> list[tuple[str, dict[str, Any]]]:
    delta = {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}}
    return [
        ("message_delta", {**delta, "usage": {"output_tokens": output_tokens}}),
        ("message_stop", {"type": "message_stop"}),
    ]


def block(index: int, content_block: dict[str, Any], delta: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("content_block_start", {"type": "content_block_start", "index": index, "content_block": content_block}),
        ("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta}),
        ("content_block_stop", {"type": "content_block_stop", "index": index}),
    ]


def client_for(server: Any) -> anthropic.Anthropic:
    transport = httpx2.MockTransport(server)
    return anthropic.Anthropic(api_key="sk-test", http_client=anthropic.DefaultHttpxClient(transport=transport))


def request(**kw: Any) -> LLMRequest:
    base: dict[str, Any] = {"stage": "searching", "system": "sys", "messages": [], "max_tokens": 500}
    return LLMRequest(**{**base, **kw})


def test_tool_loop_round_trip_with_fallback_beta_and_structured_output() -> None:
    seen: list[tuple[dict[str, str], dict[str, Any]]] = []

    def server(req: httpx2.Request) -> httpx2.Response:
        seen.append((dict(req.headers), json.loads(req.content)))
        if len(seen) == 1:
            events = [
                start("msg_1", {"input_tokens": 10, "output_tokens": 1}),
                *block(
                    0,
                    {"type": "tool_use", "id": "toolu_1", "name": "coverage", "input": {}},
                    {"type": "input_json_delta", "partial_json": '{"jurisdiction":"XA"}'},
                ),
                *finish("tool_use", 20),
            ]
        else:
            events = [
                start("msg_2", {"input_tokens": 30, "output_tokens": 1, "cache_read_input_tokens": 7}),
                *block(0, {"type": "text", "text": ""}, {"type": "text_delta", "text": '{"ok": true}'}),
                *finish("end_turn", 5),
            ]
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=sse(*events))

    llm = AnthropicLLM("sk-test", "test-model", client=client_for(server))
    first = llm.complete(
        request(messages=[{"role": "user", "content": "hi"}], tools=[TOOL], effort="medium", cache=True)
    )
    assert first.stop_reason == "tool_use" and first.tool_calls[0].input == {"jurisdiction": "XA"}

    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": first.content},  # SDK block objects are replayed as they came
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "{}"}]},
    ]
    second = llm.complete(request(stage="drafting", messages=history, json_schema=SCHEMA, effort="high"))
    assert second.text == '{"ok": true}'
    assert (second.usage.input_tokens, second.usage.cached_input_tokens) == (37, 7)

    (h1, b1), (_, b2) = seen
    assert h1["x-api-key"] == "sk-test" and h1["anthropic-beta"] == FALLBACK_BETA
    assert b1["stream"] is True and b1["fallbacks"] == "default" and b1["cache_control"] == {"type": "ephemeral"}
    assert (
        b1["tools"] == [TOOL] and b1["tool_choice"] == {"type": "auto"} and b1["output_config"] == {"effort": "medium"}
    )
    assert b2["messages"][1]["content"] == [
        {"id": "toolu_1", "input": {"jurisdiction": "XA"}, "name": "coverage", "type": "tool_use"}
    ]
    assert b2["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert "cache_control" not in b2


def test_fallbacks_can_be_switched_off() -> None:
    headers: list[dict[str, str]] = []

    def server(req: httpx2.Request) -> httpx2.Response:
        headers.append(dict(req.headers))
        events = [start("msg_3", {"input_tokens": 1, "output_tokens": 1}), *finish("end_turn", 1)]
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=sse(*events))

    AnthropicLLM("sk-test", "test-model", fallbacks="off", client=client_for(server)).complete(
        request(messages=[{"role": "user", "content": "hi"}])
    )
    assert "anthropic-beta" not in headers[0]
