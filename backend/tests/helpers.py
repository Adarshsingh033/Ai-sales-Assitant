"""
Test doubles for the AI stack.

NullLLM  — default for every test: refuses all calls, so nothing can reach a real provider and the
           graceful-degradation paths (fallback replies / templates / heuristics) are exercised.
FakeLLM  — scriptable model. Reply-task calls consume `replies` in order; analysis calls (those that request
           JSON) return `analysis`; other fast-task calls (query rewrite, summaries) return `fast_reply`.
"""
import json
from typing import Any, Callable, Dict, List, Optional, Union

from app.services.ai.llm import LLMError, LLMResult, LLMUsage, StreamEvent, ToolCall

Scripted = Union[str, LLMResult, Exception, Callable[[List[Dict[str, Any]]], Union[str, LLMResult]]]


def tool_call(name: str, **arguments: Any) -> LLMResult:
    """An LLM turn that asks to run a tool."""
    return LLMResult(
        content="",
        tool_calls=[ToolCall(id=f"call_{name}", name=name, arguments=arguments, raw_arguments=json.dumps(arguments))],
        usage=LLMUsage(prompt_tokens=80, completion_tokens=12),
        model="fake-model",
    )


class NullLLM:
    def for_task(self, task: str) -> "NullLLM":
        return self

    async def complete(self, messages, **kwargs):
        raise LLMError("LLM disabled in tests", fatal=True, code="no_api_key")

    async def stream(self, messages, **kwargs):
        raise LLMError("LLM disabled in tests", fatal=True, code="no_api_key")
        yield  # pragma: no cover  (makes this an async generator)


class _TaskView:
    def __init__(self, parent: "FakeLLM", task: str) -> None:
        self.parent, self.task = parent, task

    async def complete(self, messages, **kwargs) -> LLMResult:
        return self.parent._complete(self.task, messages, kwargs)

    async def stream(self, messages, **kwargs):
        result = self.parent._complete(self.task, messages, kwargs)
        words = result.content.split(" ") if result.content else []
        for i, word in enumerate(words):
            yield StreamEvent("token", text=word + (" " if i < len(words) - 1 else ""))
        yield StreamEvent("done", result=result)


class FakeLLM:
    def __init__(self) -> None:
        self.replies: List[Scripted] = []
        self.default_reply = "Sure, happy to help with that."
        self.analysis: Dict[str, Any] = {}
        self.analysis_error: Optional[Exception] = None
        self.fast_reply = "standalone search query"
        self.calls: List[Dict[str, Any]] = []

    def for_task(self, task: str) -> _TaskView:
        return _TaskView(self, task)

    # convenience --------------------------------------------------------
    def reply_calls(self) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["kind"] == "reply"]

    def analysis_calls(self) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["kind"] == "analysis"]

    @staticmethod
    def _result(content: str) -> LLMResult:
        return LLMResult(
            content=content, usage=LLMUsage(prompt_tokens=120, completion_tokens=max(1, len(content) // 4)),
            model="fake-model", latency_ms=5,
        )

    def _complete(self, task: str, messages: List[Dict[str, Any]], kwargs: Dict[str, Any]) -> LLMResult:
        if kwargs.get("response_format"):
            self.calls.append({"kind": "analysis", "messages": messages, "kwargs": kwargs})
            if self.analysis_error:
                raise self.analysis_error
            return self._result(json.dumps(self.analysis))
        if task == "fast":
            self.calls.append({"kind": "fast", "messages": messages, "kwargs": kwargs})
            return self._result(self.fast_reply)

        self.calls.append({"kind": "reply", "messages": [dict(m) for m in messages], "kwargs": kwargs})
        item: Scripted = self.replies.pop(0) if self.replies else self.default_reply
        if callable(item):
            item = item(messages)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, LLMResult) else self._result(item)
