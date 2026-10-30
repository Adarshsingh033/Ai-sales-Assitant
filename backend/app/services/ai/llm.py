"""
LLM Gateway — provider-agnostic client for OpenAI-compatible chat-completion APIs (Groq by default).

Reliability features
  * one pooled HTTP client per event loop (no new TLS handshake per call)
  * retries with exponential backoff + jitter, honouring Retry-After, only for transient failures
  * per-model circuit breaker, and model fallback ONLY for model-level / transient errors
    (a malformed request or a bad API key fails fast instead of burning every fallback)
  * tool/function calling, JSON mode, streaming (with tool-call deltas) and real token usage
  * `<think>…</think>` reasoning blocks are filtered out of customer-visible text
"""
import asyncio
import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from app.core.config import settings
from app.core.crypto import decrypt_secret

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
class LLMError(Exception):
    """Raised when an LLM request fails after retries / fallbacks."""

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        retriable: bool = False,
        fatal: bool = False,
        code: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retriable = retriable  # transient: retry same model, then fall back
        self.fatal = fatal  # do not try other models (bad key / bad request)
        self.code = code
        self.retry_after = retry_after


# Backwards-compatible alias used across the codebase and tests
GroqClientError = LLMError


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""


@dataclass
class LLMResult:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: LLMUsage = field(default_factory=LLMUsage)
    model: str = ""
    latency_ms: int = 0
    finish_reason: Optional[str] = None


@dataclass
class StreamEvent:
    kind: str  # "token" | "done"
    text: str = ""
    result: Optional[LLMResult] = None


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (multilingual-safe): ~3.5 chars per token."""
    return max(1, int(len(text) / 3.5)) if text else 0


def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        total += estimate_tokens(str(m.get("content") or "")) + 4
        for tc in m.get("tool_calls") or []:
            total += estimate_tokens(json.dumps(tc.get("function", {})))
    return total


# ---------------------------------------------------------------------------
# Shared HTTP client (one per running event loop)
# ---------------------------------------------------------------------------
_http_clients: Dict[int, httpx.AsyncClient] = {}


def get_http_client() -> httpx.AsyncClient:
    loop_id = id(asyncio.get_running_loop())
    client = _http_clients.get(loop_id)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.LLM_TIMEOUT_SECONDS, connect=5.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        _http_clients[loop_id] = client
    return client


async def close_http_clients() -> None:
    for client in list(_http_clients.values()):
        try:
            await client.aclose()
        except Exception:  # pragma: no cover - best effort at shutdown
            pass
    _http_clients.clear()


# ---------------------------------------------------------------------------
# Circuit breaker (per base_url + model)
# ---------------------------------------------------------------------------
_breakers: Dict[str, Dict[str, float]] = {}


def _breaker_open(key: str) -> bool:
    state = _breakers.get(key)
    return bool(state and state.get("open_until", 0) > time.monotonic())


def _breaker_failure(key: str) -> None:
    state = _breakers.setdefault(key, {"failures": 0, "open_until": 0})
    state["failures"] += 1
    if state["failures"] >= settings.LLM_BREAKER_FAILURES:
        state["open_until"] = time.monotonic() + settings.LLM_BREAKER_COOLDOWN_SECONDS
        state["failures"] = 0
        logger.warning("LLM circuit breaker opened for %s", key)


def _breaker_trip(key: str, seconds: float) -> None:
    """Open the breaker for a fixed period (used when a model is simply not available to this API key)."""
    _breakers[key] = {"failures": 0, "open_until": time.monotonic() + seconds}
    logger.warning("LLM model unavailable for this key; skipping %s for %.0fs", key.split("|")[1], seconds)


def _breaker_success(key: str) -> None:
    _breakers.pop(key, None)


def reset_breakers() -> None:
    _breakers.clear()


# ---------------------------------------------------------------------------
# Reasoning-block filter for streamed text
# ---------------------------------------------------------------------------
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    return _THINK_BLOCK.sub("", text or "").strip()


class _ThinkFilter:
    """Streaming filter that swallows <think>…</think> spans, even when tags are split across chunks."""

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False

    def feed(self, text: str) -> str:
        self._buf += text
        out: List[str] = []
        while self._buf:
            lowered = self._buf.lower()
            if self._inside:
                end = lowered.find("</think>")
                if end == -1:
                    self._buf = self._buf[-8:]  # keep a possible partial closing tag
                    break
                self._buf = self._buf[end + len("</think>"):]
                self._inside = False
            else:
                start = lowered.find("<think>")
                if start == -1:
                    # hold back a possible partial opening tag at the end
                    hold = 0
                    for i in range(1, min(len("<think>"), len(self._buf)) + 1):
                        if "<think>".startswith(lowered[-i:]):
                            hold = i
                    if hold:
                        out.append(self._buf[:-hold])
                        self._buf = self._buf[-hold:]
                    else:
                        out.append(self._buf)
                        self._buf = ""
                    break
                out.append(self._buf[:start])
                self._buf = self._buf[start + len("<think>"):]
                self._inside = True
        return "".join(out)

    def flush(self) -> str:
        rest = "" if self._inside else self._buf
        self._buf = ""
        return rest


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class LLMClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        fallback_models: Optional[List[str]] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        http: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.GROQ_API_KEY
        self.model = model or settings.GROQ_MODEL
        self.base_url = (base_url or settings.LLM_BASE_URL).rstrip("/")
        self.fallback_models = (
            fallback_models if fallback_models is not None else settings.llm_fallback_models_list
        )
        self.timeout = timeout if timeout is not None else settings.LLM_TIMEOUT_SECONDS
        self.max_retries = max_retries if max_retries is not None else settings.LLM_MAX_RETRIES
        self._http = http

    # -- helpers ------------------------------------------------------------
    @property
    def _client(self) -> httpx.AsyncClient:
        return self._http or get_http_client()

    @property
    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _breaker_key(self, model: str) -> str:
        fp = hashlib.sha256((self.api_key or "").encode()).hexdigest()[:8]  # model access differs per API key
        return f"{self.base_url}|{model}|{fp}"

    def _models(self, override: Optional[str]) -> List[str]:
        primary = override or self.model
        ordered = [primary]
        for m in self.fallback_models:
            if m not in ordered:
                ordered.append(m)
        return ordered

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _payload(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        tools: Optional[List[Dict[str, Any]]],
        response_format: Optional[Dict[str, Any]],
        stream: bool,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format
        if stream:
            payload["stream"] = True
        if model.startswith("openai/gpt-oss"):
            payload["reasoning_effort"] = "low"  # keep latency/cost down for sales chat
        return payload

    def _require_key(self) -> None:
        if not self.api_key:
            raise LLMError(
                "Groq API key is not configured. Please set GROQ_API_KEY in your environment or tenant settings.",
                fatal=True,
                code="no_api_key",
            )

    @staticmethod
    def _classify(status: int, body: str, retry_after: Optional[float]) -> LLMError:
        snippet = body[:300]
        low = body.lower()
        if status in (401, 403):
            return LLMError(f"LLM authentication failed ({status})", status=status, fatal=True, code="auth")
        if status == 429 or status >= 500:
            return LLMError(
                f"LLM transient error ({status}): {snippet}", status=status, retriable=True, retry_after=retry_after
            )
        if status == 400 and "tool_use_failed" in low:
            return LLMError(f"LLM tool call failed: {snippet}", status=status, code="tool_use_failed")
        if status == 404 or (status in (400, 422) and ("model" in low and ("not" in low or "decommission" in low))):
            return LLMError(f"LLM model unavailable ({status}): {snippet}", status=status, code="model_unavailable")
        return LLMError(f"LLM request rejected ({status}): {snippet}", status=status, fatal=True, code="bad_request")

    @staticmethod
    def _retry_after(resp: httpx.Response) -> Optional[float]:
        raw = resp.headers.get("retry-after")
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    def _backoff(self, attempt: int, retry_after: Optional[float]) -> float:
        if retry_after is not None:
            return min(retry_after, 8.0)
        return min(0.5 * (2 ** attempt), 4.0) + random.uniform(0, 0.25)

    @staticmethod
    def _parse_tool_calls(raw_calls: List[Dict[str, Any]]) -> List[ToolCall]:
        calls: List[ToolCall] = []
        for i, rc in enumerate(raw_calls or []):
            fn = rc.get("function") or {}
            raw_args = fn.get("arguments") or ""
            try:
                args = json.loads(raw_args) if raw_args else {}
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            calls.append(
                ToolCall(id=rc.get("id") or f"call_{i}", name=fn.get("name") or "", arguments=args, raw_arguments=raw_args)
            )
        return calls

    @staticmethod
    def _parse_usage(data: Dict[str, Any]) -> Optional[LLMUsage]:
        usage = data.get("usage") or (data.get("x_groq") or {}).get("usage")
        if not usage:
            return None
        return LLMUsage(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )

    # -- non-streaming ------------------------------------------------------
    async def complete(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
        tools: Optional[List[Dict[str, Any]]] = None,
        response_format: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
    ) -> LLMResult:
        self._require_key()
        last_error: Optional[LLMError] = None
        for current_model in self._models(model):
            key = self._breaker_key(current_model)
            if _breaker_open(key):
                continue
            payload = self._payload(
                current_model, messages, temperature=temperature, max_tokens=max_tokens,
                tools=tools, response_format=response_format, stream=False,
            )
            try:
                result = await self._complete_with_retries(key, payload)
                _breaker_success(key)
                return result
            except LLMError as exc:
                last_error = exc
                if exc.code == "tool_use_failed" and tools:
                    # Malformed function call from the model: answer without tools instead of failing the turn.
                    logger.warning("Model %s produced an invalid tool call; retrying without tools", current_model)
                    payload.pop("tools", None)
                    payload.pop("tool_choice", None)
                    try:
                        return await self._complete_with_retries(key, payload)
                    except LLMError as exc2:
                        last_error = exc2
                if exc.fatal:
                    raise
                if exc.code == "model_unavailable":
                    _breaker_trip(key, settings.LLM_MODEL_UNAVAILABLE_COOLDOWN_SECONDS)
                elif exc.retriable:
                    _breaker_failure(key)
                logger.warning("LLM model '%s' failed: %s", current_model, exc)
        raise last_error or LLMError("All LLM models are unavailable.", retriable=True)

    async def _complete_with_retries(self, key: str, payload: Dict[str, Any]) -> LLMResult:
        error: Optional[LLMError] = None
        for attempt in range(self.max_retries + 1):
            started = time.monotonic()
            try:
                resp = await self._client.post(self._url, headers=self._headers(), json=payload, timeout=self.timeout)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                error = LLMError(f"LLM network error: {exc!r}", retriable=True)
            else:
                if resp.status_code == 200:
                    return self._build_result(resp.json(), payload["model"], started)
                error = self._classify(resp.status_code, resp.text, self._retry_after(resp))
            if not error.retriable or attempt >= self.max_retries:
                raise error
            await asyncio.sleep(self._backoff(attempt, error.retry_after))
        raise error or LLMError("LLM request failed")  # pragma: no cover

    def _build_result(self, data: Dict[str, Any], model: str, started: float) -> LLMResult:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("LLM returned no choices", retriable=True)
        message = choices[0].get("message") or {}
        content = strip_reasoning(message.get("content") or "")
        tool_calls = self._parse_tool_calls(message.get("tool_calls") or [])
        usage = self._parse_usage(data)
        if usage is None:
            usage = LLMUsage(completion_tokens=estimate_tokens(content), estimated=True)
        return LLMResult(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            model=data.get("model") or model,
            latency_ms=int((time.monotonic() - started) * 1000),
            finish_reason=choices[0].get("finish_reason"),
        )

    # -- streaming ----------------------------------------------------------
    async def stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
    ) -> AsyncIterator[StreamEvent]:
        """
        Yields StreamEvent("token") for customer-visible text and a final StreamEvent("done") carrying the
        aggregated LLMResult (tool calls + usage). Retries/fallbacks happen only before the first token.
        """
        self._require_key()
        last_error: Optional[LLMError] = None
        for current_model in self._models(model):
            key = self._breaker_key(current_model)
            if _breaker_open(key):
                continue
            payload = self._payload(
                current_model, messages, temperature=temperature, max_tokens=max_tokens,
                tools=tools, response_format=None, stream=True,
            )
            for attempt in range(self.max_retries + 1):
                emitted = False
                try:
                    async for event in self._stream_once(payload):
                        if event.kind == "token":
                            emitted = True
                        yield event
                    _breaker_success(key)
                    return
                except LLMError as exc:
                    last_error = exc
                    if emitted:
                        raise  # tokens already reached the caller; cannot transparently retry
                    if exc.code == "tool_use_failed" and payload.get("tools"):
                        payload.pop("tools", None)
                        payload.pop("tool_choice", None)
                        continue
                    if exc.fatal:
                        raise
                    if exc.retriable and attempt < self.max_retries:
                        await asyncio.sleep(self._backoff(attempt, exc.retry_after))
                        continue
                    if exc.code == "model_unavailable":
                        _breaker_trip(key, settings.LLM_MODEL_UNAVAILABLE_COOLDOWN_SECONDS)
                    elif exc.retriable:
                        _breaker_failure(key)
                    logger.warning("LLM stream model '%s' failed: %s", current_model, exc)
                    break
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = LLMError(f"LLM network error: {exc!r}", retriable=True)
                    if emitted:
                        raise last_error from exc
                    if attempt < self.max_retries:
                        await asyncio.sleep(self._backoff(attempt, None))
                        continue
                    _breaker_failure(key)
                    break
        raise last_error or LLMError("All LLM models are unavailable.", retriable=True)

    async def _stream_once(self, payload: Dict[str, Any]) -> AsyncIterator[StreamEvent]:
        started = time.monotonic()
        text_parts: List[str] = []
        tool_acc: Dict[int, Dict[str, Any]] = {}
        usage: Optional[LLMUsage] = None
        finish: Optional[str] = None
        model_name = payload["model"]
        think = _ThinkFilter()

        async with self._client.stream(
            "POST", self._url, headers=self._headers(), json=payload, timeout=self.timeout
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode(errors="ignore")
                raise self._classify(resp.status_code, body, self._retry_after(resp))
            async for raw_line in resp.aiter_lines():
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                model_name = chunk.get("model") or model_name
                usage = self._parse_usage(chunk) or usage
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                finish = choices[0].get("finish_reason") or finish
                content = delta.get("content")
                if content:
                    visible = think.feed(content)
                    if visible:
                        text_parts.append(visible)
                        yield StreamEvent("token", text=visible)
                for tc in delta.get("tool_calls") or []:
                    slot = tool_acc.setdefault(tc.get("index", 0), {"id": "", "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]

        tail = think.flush()
        if tail:
            text_parts.append(tail)
            yield StreamEvent("token", text=tail)
        content_full = "".join(text_parts).strip()
        tool_calls = self._parse_tool_calls([tool_acc[i] for i in sorted(tool_acc)])
        if usage is None:
            usage = LLMUsage(completion_tokens=estimate_tokens(content_full), estimated=True)
        yield StreamEvent(
            "done",
            result=LLMResult(
                content=content_full,
                tool_calls=tool_calls,
                usage=usage,
                model=model_name,
                latency_ms=int((time.monotonic() - started) * 1000),
                finish_reason=finish,
            ),
        )

    # -- convenience wrappers (kept for existing call sites) -----------------
    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> str:
        result = await self.complete(
            messages, temperature=temperature, max_tokens=max_tokens, response_format=response_format
        )
        return result.content

    async def stream_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        async for event in self.stream(messages, temperature=temperature, max_tokens=max_tokens):
            if event.kind == "token":
                yield event.text


# ---------------------------------------------------------------------------
# Factory (tenant-aware) + test override
# ---------------------------------------------------------------------------
_llm_override: Optional[Any] = None


def set_llm_override(client: Optional[Any]) -> None:
    """Install a fake/mock LLM client for tests (pass None to clear)."""
    global _llm_override
    _llm_override = client


def build_llm_client(config: Optional[Any], *, task: str = "reply") -> LLMClient:
    """
    Build the gateway for a tenant. `task` selects the model tier:
      "reply" → tenant's quality model; "fast" → cheap model for rewrite / analysis / summary.
    """
    if _llm_override is not None:
        return _llm_override.for_task(task) if hasattr(_llm_override, "for_task") else _llm_override
    api_key = (decrypt_secret(getattr(config, "groq_api_key", None)) if config else None) or settings.GROQ_API_KEY
    reply_model = (getattr(config, "groq_model", None) if config else None) or settings.GROQ_MODEL
    if task == "fast":
        fast_model = (getattr(config, "fast_model", None) if config else None) or settings.LLM_FAST_MODEL
        # If the cheap model is not available to this key, fall back to the tenant's own (known-good) model first.
        fallbacks = [reply_model] + [m for m in settings.llm_fallback_models_list if m != reply_model]
        return LLMClient(api_key=api_key, model=fast_model, fallback_models=fallbacks)
    return LLMClient(api_key=api_key, model=reply_model)
