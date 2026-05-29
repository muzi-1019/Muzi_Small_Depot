from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from app.core.config import settings


class LLMClient:
    @staticmethod
    def _chat_url() -> str:
        base_url = (settings.openai_api_base or "").rstrip("/")
        if not base_url:
            raise RuntimeError("未配置大模型接口地址，请设置 RAG_OPENAI_API_BASE")
        return f"{base_url}/chat/completions"

    @staticmethod
    def _headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.openai_api_key}"}

    @staticmethod
    def chat_payload(messages: list[dict[str, Any]], temperature: float = 0.4, max_tokens: int | None = None, stream: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": settings.llm_model_name,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stream:
            payload["stream"] = True
        return payload

    @staticmethod
    def chat(messages: list[dict[str, Any]], temperature: float = 0.4, max_tokens: int | None = None, timeout: float = 120.0) -> dict[str, Any]:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            resp = client.post(
                LLMClient._chat_url(),
                headers=LLMClient._headers(),
                json=LLMClient.chat_payload(messages, temperature=temperature, max_tokens=max_tokens),
            )
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def chat_stream(messages: list[dict[str, Any]], temperature: float = 0.4, timeout: float = 120.0) -> Iterator[str]:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            with client.stream(
                "POST",
                LLMClient._chat_url(),
                headers=LLMClient._headers(),
                json=LLMClient.chat_payload(messages, temperature=temperature, stream=True),
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    yield line

    @staticmethod
    def extract_message_content(data: dict[str, Any]) -> str:
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM response: {data}") from exc
