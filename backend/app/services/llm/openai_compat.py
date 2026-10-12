"""Any server that speaks the OpenAI chat-completions protocol (LM Studio, vLLM, llama.cpp server, LocalAI, hosted APIs).

Optional: Ollama remains the default and nothing here is required to run the project.
"""
import time

import httpx

from app.services.llm.base import LLMError, LLMProvider, LLMResult


class OpenAICompatProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str = "", *, timeout: float = 120.0,
                 default_temperature: float = 0.1, default_max_tokens: int = 350):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.default_temperature = default_temperature
        self.default_max_tokens = default_max_tokens

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def chat(self, messages, *, temperature=None, max_tokens=None, json_mode=False) -> LLMResult:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.default_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.default_max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        start = time.perf_counter()
        try:
            resp = httpx.post(f"{self.base_url}/chat/completions", json=payload, headers=self._headers(), timeout=self.timeout)
            resp.raise_for_status()
            body = resp.json()
            text = body["choices"][0]["message"]["content"] or ""
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            raise LLMError(f"OpenAI-compatible request failed: {exc}") from exc
        usage = body.get("usage") or {}
        return LLMResult(text.strip(), self.model, (time.perf_counter() - start) * 1000,
                         usage.get("prompt_tokens"), usage.get("completion_tokens"))

    def health(self) -> dict:
        try:
            resp = httpx.get(f"{self.base_url}/models", headers=self._headers(), timeout=5.0)
            resp.raise_for_status()
            ids = [m.get("id", "") for m in resp.json().get("data", [])]
        except (httpx.HTTPError, ValueError) as exc:
            return {"ok": False, "provider": self.name, "model": self.model, "error": str(exc)}
        found = not ids or self.model in ids  # some servers do not list models: do not fail on that
        return {"ok": found, "provider": self.name, "model": self.model, "error": None if found else f"model '{self.model}' not offered by the server"}
