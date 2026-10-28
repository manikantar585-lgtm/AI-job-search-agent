"""Thin OpenAI-compatible client. Returns None when no key / on error so callers can fall back."""
import httpx

from . import config


def raw_chat(messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 800) -> dict | None:
    if not config.LLM_API_KEY:
        return None
    body = {"model": config.LLM_MODEL, "temperature": 0.2, "max_tokens": max_tokens, "messages": messages}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    try:
        r = httpx.post(f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                       headers={"Authorization": f"Bearer {config.LLM_API_KEY}"}, json=body, timeout=60)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]
    except Exception:
        return None


def chat(messages: list[dict], max_tokens: int = 800) -> str | None:
    msg = raw_chat(messages, max_tokens=max_tokens)
    return (msg or {}).get("content") or None
