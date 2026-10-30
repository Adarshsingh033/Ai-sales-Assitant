"""LLM gateway: retries, fallbacks, circuit breaker, tool calls, reasoning filter, streaming, real usage."""
import json
from typing import Callable, List

import httpx
import pytest

from app.services.ai import llm as llm_mod
from app.services.ai.llm import LLMClient, LLMError, _ThinkFilter, strip_reasoning

OK_BODY = {
    "model": "served-model",
    "choices": [{"message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 31, "completion_tokens": 4},
}


def make_client(handler: Callable[[httpx.Request], httpx.Response], **kw) -> LLMClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kw.setdefault("fallback_models", ["fallback-model"])
    kw.setdefault("max_retries", 2)
    return LLMClient(api_key="k", model="primary-model", base_url="https://llm.test/v1", http=http, **kw)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    sleeps: List[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(llm_mod.asyncio, "sleep", fake_sleep)
    return sleeps


@pytest.mark.asyncio
async def test_success_reports_real_usage_and_model():
    client = make_client(lambda req: httpx.Response(200, json=OK_BODY))
    res = await client.complete([{"role": "user", "content": "hi"}])
    assert res.content == "Hello!"
    assert (res.usage.prompt_tokens, res.usage.completion_tokens, res.usage.estimated) == (31, 4, False)
    assert res.model == "served-model"


@pytest.mark.asyncio
async def test_missing_api_key_message():
    client = LLMClient(api_key="", model="m")
    with pytest.raises(LLMError, match="Groq API key is not configured"):
        await client.complete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_retries_rate_limit_honouring_retry_after(_no_real_sleep):
    calls = []

    def handler(req):
        calls.append(json.loads(req.content)["model"])
        if len(calls) < 3:
            return httpx.Response(429, headers={"retry-after": "2"}, text="slow down")
        return httpx.Response(200, json=OK_BODY)

    res = await make_client(handler).complete([{"role": "user", "content": "hi"}])
    assert res.content == "Hello!"
    assert calls == ["primary-model"] * 3  # same model retried, no fallback burned
    assert _no_real_sleep == [2.0, 2.0]


@pytest.mark.asyncio
async def test_falls_back_when_model_is_unavailable():
    calls = []

    def handler(req):
        model = json.loads(req.content)["model"]
        calls.append(model)
        if model == "primary-model":
            return httpx.Response(404, json={"error": {"message": "The model `primary-model` does not exist"}})
        return httpx.Response(200, json=OK_BODY)

    res = await make_client(handler).complete([{"role": "user", "content": "hi"}])
    assert res.content == "Hello!"
    assert calls == ["primary-model", "fallback-model"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,detail", [(401, "bad key"), (403, "forbidden"), (400, "messages must be non-empty")])
async def test_auth_and_bad_request_fail_fast_without_burning_fallbacks(status, detail):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(status, json={"error": {"message": detail}})

    with pytest.raises(LLMError) as exc:
        await make_client(handler).complete([{"role": "user", "content": "hi"}])
    assert exc.value.fatal is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_transient_failures_exhaust_retries_then_fall_back():
    calls = []

    def handler(req):
        model = json.loads(req.content)["model"]
        calls.append(model)
        return httpx.Response(503, text="overloaded") if model == "primary-model" else httpx.Response(200, json=OK_BODY)

    res = await make_client(handler, max_retries=1).complete([{"role": "user", "content": "hi"}])
    assert res.content == "Hello!"
    assert calls == ["primary-model", "primary-model", "fallback-model"]


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_repeated_failures():
    calls = []

    def handler(req):
        model = json.loads(req.content)["model"]
        calls.append(model)
        return httpx.Response(500, text="boom") if model == "primary-model" else httpx.Response(200, json=OK_BODY)

    client = make_client(handler, max_retries=0)
    for _ in range(3):
        await client.complete([{"role": "user", "content": "hi"}])
    assert calls.count("primary-model") == 3
    calls.clear()
    await client.complete([{"role": "user", "content": "hi"}])
    assert calls == ["fallback-model"]  # breaker is open: the failing model is skipped entirely


@pytest.mark.asyncio
async def test_all_models_down_raises_retriable_error():
    client = make_client(lambda req: httpx.Response(503, text="down"), max_retries=0)
    with pytest.raises(LLMError) as exc:
        await client.complete([{"role": "user", "content": "hi"}])
    assert exc.value.retriable is True


@pytest.mark.asyncio
async def test_tool_calls_are_parsed():
    body = {
        "choices": [{
            "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "check_stock", "arguments": '{"product": "CRM"}'}}],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json=body)

    res = await make_client(handler).complete([{"role": "user", "content": "stock?"}], tools=[{"type": "function"}])
    assert seen["tool_choice"] == "auto"
    assert [(t.id, t.name, t.arguments) for t in res.tool_calls] == [("c1", "check_stock", {"product": "CRM"})]


@pytest.mark.asyncio
async def test_invalid_tool_call_is_retried_without_tools():
    attempts = []

    def handler(req):
        payload = json.loads(req.content)
        attempts.append("tools" in payload)
        if "tools" in payload:
            return httpx.Response(400, json={"error": {"message": "Failed to call a function.", "code": "tool_use_failed"}})
        return httpx.Response(200, json=OK_BODY)

    res = await make_client(handler).complete([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert res.content == "Hello!"
    assert attempts == [True, False]


def test_reasoning_blocks_are_removed():
    assert strip_reasoning("<think>plan the pitch</think>Hello there!") == "Hello there!"
    flt = _ThinkFilter()
    out = "".join(flt.feed(c) for c in ["Hel", "lo <thi", "nk>secret</th", "ink> wor", "ld"]) + flt.flush()
    assert out == "Hello  world"


@pytest.mark.asyncio
async def test_stream_filters_reasoning_and_assembles_tool_calls():
    chunks = [
        {"choices": [{"delta": {"content": "<think>hmm</think>Sure"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c9", "function": {"name": "calculate_discount", "arguments": '{"product":'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ' "CRM", "quantity": 3}'}}]}, "finish_reason": "tool_calls"}],
         "usage": {"prompt_tokens": 20, "completion_tokens": 9}},
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    client = make_client(lambda req: httpx.Response(200, content=body.encode()))
    tokens, done = [], None
    async for ev in client.stream([{"role": "user", "content": "x"}], tools=[{"type": "function"}]):
        (tokens.append(ev.text) if ev.kind == "token" else None)
        if ev.kind == "done":
            done = ev.result
    assert "".join(tokens) == "Sure"
    assert done.tool_calls[0].name == "calculate_discount"
    assert done.tool_calls[0].arguments == {"product": "CRM", "quantity": 3}
    assert done.usage.total_tokens == 29


@pytest.mark.asyncio
async def test_stream_retries_before_first_token():
    attempts = []

    def handler(req):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\ndata: [DONE]\n\n')

    client = make_client(handler)
    text = "".join([ev.text async for ev in client.stream([{"role": "user", "content": "x"}]) if ev.kind == "token"])
    assert text == "Hi" and len(attempts) == 2


@pytest.mark.asyncio
async def test_a_model_this_key_cannot_access_is_skipped_for_a_long_time():
    """Live finding: an org without access to a model re-tried the 404 on EVERY call, adding seconds of latency."""
    calls = []

    def handler(req):
        model = json.loads(req.content)["model"]
        calls.append(model)
        if model == "primary-model":
            return httpx.Response(404, json={"error": {"message": "The model `primary-model` does not exist or you do not have access", "code": "model_not_found"}})
        return httpx.Response(200, json=OK_BODY)

    client = make_client(handler)
    for _ in range(3):
        await client.complete([{"role": "user", "content": "hi"}])
    assert calls == ["primary-model", "fallback-model", "fallback-model", "fallback-model"]  # probed once, then skipped

    # a different API key has its own access rights, so it probes again
    other = LLMClient(api_key="other-key", model="primary-model", base_url="https://llm.test/v1",
                      http=client._http, fallback_models=["fallback-model"])
    calls.clear()
    await other.complete([{"role": "user", "content": "hi"}])
    assert calls == ["primary-model", "fallback-model"]


def test_fast_tasks_fall_back_to_the_tenants_own_model_first(monkeypatch):
    from types import SimpleNamespace

    llm_mod.set_llm_override(None)
    monkeypatch.setattr(llm_mod.settings, "GROQ_API_KEY", "k")
    cfg = SimpleNamespace(groq_api_key=None, groq_model="openai/gpt-oss-20b", fast_model=None)
    fast = llm_mod.build_llm_client(cfg, task="fast")
    assert fast.model == llm_mod.settings.LLM_FAST_MODEL
    assert fast.fallback_models[0] == "openai/gpt-oss-20b"
    reply = llm_mod.build_llm_client(cfg, task="reply")
    assert reply.model == "openai/gpt-oss-20b"
