"""
OpenRouter API client for Voksy AI Agent orchestrator.
Uses system API key from settings.OPENROUTER_API_KEY.
"""
import json
from typing import Optional, List, Dict, Any, AsyncIterator
import httpx
from backend.core.config import settings
from backend.core.logging import get_logger

logger = get_logger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


def _is_anthropic_model(model: str) -> bool:
    return (model or "").lower().startswith("anthropic/")


def _with_cache_control(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Расставить маркеры prompt-кэширования для Anthropic-моделей.

    OpenAI/DeepSeek/Gemini кэшируют префикс автоматически, Anthropic требует
    явные breakpoint'ы cache_control (OpenRouter прокидывает их как есть).
    Ставим два: на system (кэширует tools + system — статичную часть) и на
    последнее сообщение (кэширует растущую историю внутри цикла tool calls).

    Возвращает копию списка — исходные messages оркестратора не мутируются.
    """
    prepared = [dict(m) for m in messages]

    def _mark(msg: Dict[str, Any]) -> bool:
        content = msg.get("content")
        if isinstance(content, str):
            if not content:
                return False
            msg["content"] = [{
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }]
            return True
        if isinstance(content, list) and content and isinstance(content[-1], dict):
            new_content = list(content)
            last = dict(new_content[-1])
            last["cache_control"] = {"type": "ephemeral"}
            new_content[-1] = last
            msg["content"] = new_content
            return True
        return False

    for msg in prepared:
        if msg.get("role") == "system":
            _mark(msg)
            break

    for msg in reversed(prepared):
        if msg.get("role") != "system" and _mark(msg):
            break

    return prepared


def _log_usage(model: str, usage: Optional[Dict[str, Any]]) -> None:
    """Лог фактического расхода токенов, включая попадание в кэш провайдера."""
    if not usage:
        return
    details = usage.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens", 0) or 0)
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    hit = f"{cached * 100 // prompt}%" if prompt and cached else "0%"
    logger.info(
        f"[OPENROUTER] usage {model}: prompt={prompt} (cached={cached}, hit={hit}), "
        f"completion={int(usage.get('completion_tokens', 0) or 0)}"
    )


class OpenRouterClient:
    def __init__(self):
        self.api_key = settings.OPENROUTER_API_KEY
        if not self.api_key:
            logger.warning("[OPENROUTER] OPENROUTER_API_KEY not set in env!")

    async def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = "auto",
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float = 90.0,
    ) -> Dict[str, Any]:
        """
        Returns full response dict from OpenRouter.
        Raises Exception on error.
        """
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not configured")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://voicyfy.ru",
            "X-Title": "Voksy AI Agent",
        }

        if _is_anthropic_model(model):
            messages = _with_cache_control(messages)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            # Детальный usage (в т.ч. cached_tokens) для контроля кэша промпта.
            "usage": {"include": True},
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                OPENROUTER_API_URL,
                json=payload,
                headers=headers
            )
            response.raise_for_status()
            data = response.json()
            _log_usage(model, data.get("usage"))
            return data

    async def chat_completion_stream(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = "auto",
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float = 90.0,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Streaming version of chat_completion. Async-generator that yields raw
        SSE chunk dicts from OpenRouter (`{"choices":[{"delta":{...},"finish_reason":...}], "usage":...}`).

        Reconstruction of tool_calls from delta fragments is the caller's job —
        this client stays thin and just parses/forwards chunks.

        Raises on network/HTTP errors (caught by the orchestrator).
        """
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not configured")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://voicyfy.ru",
            "X-Title": "Voksy AI Agent",
        }

        if _is_anthropic_model(model):
            messages = _with_cache_control(messages)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            # OpenRouter returns usage in the final chunk when this is set.
            "usage": {"include": True},
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", OPENROUTER_API_URL, json=payload, headers=headers
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    # OpenRouter keep-alive comments (e.g. ": OPENROUTER PROCESSING")
                    if line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError as e:
                        logger.warning(f"[OPENROUTER] Stream JSON parse error: {e} | line: {data[:200]}")
                        continue
                    if chunk.get("usage"):
                        _log_usage(model, chunk.get("usage"))
                    yield chunk


_client: Optional[OpenRouterClient] = None


def get_openrouter_client() -> OpenRouterClient:
    global _client
    if _client is None:
        _client = OpenRouterClient()
    return _client
