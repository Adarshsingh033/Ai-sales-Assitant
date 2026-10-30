"""
Groq API LLM Client.
Provides high-speed, zero-cost conversational inference using Groq free tier API.
Compatible with standard OpenAI chat completions format.
"""
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqClientError(Exception):
    """Raised when a Groq API request fails."""
    pass


class GroqClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.GROQ_API_KEY
        self.model = model or settings.GROQ_MODEL
        self.timeout = timeout

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Execute an asynchronous chat completion request via Groq.
        """
        if not self.api_key:
            raise GroqClientError(
                "Groq API key is not configured. Please set GROQ_API_KEY in your environment or tenant settings."
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if response_format:
            payload["response_format"] = response_format

        models_to_try = [self.model]
        for fallback in ["openai/gpt-oss-20b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]:
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        last_error = None
        for current_model in models_to_try:
            payload["model"] = current_model
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(GROQ_BASE_URL, headers=headers, json=payload)

                if response.status_code == 200:
                    data = response.json()
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "").strip()

                logger.warning("Groq model '%s' failed (HTTP %s): %s", current_model, response.status_code, response.text[:150])
                last_error = f"Groq API error ({response.status_code}): {response.text}"
            except httpx.RequestError as exc:
                logger.error("Network error communicating with Groq for model '%s': %s", current_model, exc)
                last_error = str(exc)

        raise GroqClientError(last_error or "All Groq model attempts failed.")

    async def stream_chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> AsyncGenerator[str, None]:
        """
        Execute an asynchronous streaming chat completion request via Groq.
        Yields individual text content chunks as they arrive from the API.
        """
        if not self.api_key:
            raise GroqClientError(
                "Groq API key is not configured. Please set GROQ_API_KEY in your environment or tenant settings."
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        models_to_try = [self.model]
        for fallback in ["openai/gpt-oss-20b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]:
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        last_error = None
        for current_model in models_to_try:
            payload["model"] = current_model
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    async with client.stream("POST", GROQ_BASE_URL, headers=headers, json=payload) as response:
                        if response.status_code == 200:
                            async for raw_line in response.aiter_lines():
                                line = raw_line.strip()
                                if not line or not line.startswith("data: "):
                                    continue
                                data_str = line[6:].strip()
                                if data_str == "[DONE]":
                                    return
                                try:
                                    chunk = json.loads(data_str)
                                    choices = chunk.get("choices", [])
                                    if choices:
                                        delta = choices[0].get("delta", {})
                                        content = delta.get("content")
                                        if content:
                                            yield content
                                except Exception:
                                    continue
                            return

                        error_bytes = await response.aread()
                        logger.warning(
                            "Groq stream model '%s' failed (HTTP %s): %s",
                            current_model,
                            response.status_code,
                            error_bytes.decode(errors="ignore")[:150],
                        )
                        last_error = f"Groq API error ({response.status_code})"
            except httpx.RequestError as exc:
                logger.error("Network error streaming from Groq for model '%s': %s", current_model, exc)
                last_error = str(exc)

        raise GroqClientError(last_error or "All Groq model stream attempts failed.")
