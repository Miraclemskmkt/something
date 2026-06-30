"""LLM HTTP 客户端：Ollama / OpenAI 兼容接口共用。"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)


def call_llm_chat(
    prompt: str,
    *,
    model: str | None = None,
    timeout: int | None = None,
    temperature: float = 0.1,
    system: str | None = None,
) -> tuple[str | None, str | None]:
    """
    同步调用 LLM chat，返回 (content, error)。
    model 默认 settings.llm_model；分类器可传 settings.llm_classify_model。
    """
    if not settings.llm_enabled:
        return None, "disabled"

    use_model = model or settings.llm_model
    use_timeout = timeout or settings.llm_timeout_sec
    base = (settings.llm_api_base or "").rstrip("/")

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body: dict[str, Any] = {
        "model": use_model,
        "messages": messages,
        "temperature": temperature,
    }

    if settings.llm_provider == "ollama":
        url = f"{base}/api/chat"
        keep_alive: str | int = settings.llm_keep_alive
        if isinstance(keep_alive, str) and keep_alive.lstrip("-").isdigit():
            keep_alive = int(keep_alive)
        body = {
            "model": use_model,
            "messages": messages,
            "stream": False,
            "keep_alive": keep_alive,
        }
    else:
        url = f"{base}/chat/completions"

    try:
        with httpx.Client(timeout=use_timeout) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        if settings.llm_provider == "ollama":
            raw = data.get("message", {}).get("content", "")
        else:
            raw = data["choices"][0]["message"]["content"]
        return (raw or "").strip() or None, None
    except httpx.TimeoutException:
        return None, "timeout"
    except Exception as e:
        logger.debug("LLM call failed: %s", e)
        return None, str(e)
