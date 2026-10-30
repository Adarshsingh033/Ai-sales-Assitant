"""
Groq API client — compatibility shim.

The implementation moved to `app.services.ai.llm` (provider-agnostic gateway with retries, circuit breaker,
tool calling and real usage accounting). `GroqClient` remains as the Groq-flavoured name for that client.
"""
from app.services.ai.llm import LLMClient, LLMError as GroqClientError  # noqa: F401

GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqClient(LLMClient):
    """OpenAI-compatible client pointed at Groq by default."""
