import time

import httpx

from app.services.llm.base import LLMError, LLMProvider, LLMResult


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        num_ctx: int = 4096,
        timeout: float = 120.0,
        default_temperature: float = 0.1,
        default_max_tokens: int = 350,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.timeout = timeout
        self.default_temperature = default_temperature
        self.default_max_tokens = default_max_tokens

    def chat(self, messages, *, temperature=None, max_tokens=None, json_mode=False) -> LLMResult:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": "10m",  # keep weights in memory between requests
            "options": {
                "temperature": self.default_temperature if temperature is None else temperature,
                "num_predict": max_tokens or self.default_max_tokens,
                "num_ctx": self.num_ctx,
            },
        }
        if json_mode:
            payload["format"] = "json"
        start = time.perf_counter()
        try:
            resp = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc
        text = (body.get("message") or {}).get("content", "")
        return LLMResult(
            text=text.strip(),
            model=self.model,
            latency_ms=(time.perf_counter() - start) * 1000,
            prompt_tokens=body.get("prompt_eval_count"),
            completion_tokens=body.get("eval_count"),
        )

    def health(self) -> dict:
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            resp.raise_for_status()
            names = [m.get("name", "") for m in resp.json().get("models", [])]
        except (httpx.HTTPError, ValueError) as exc:
            return {"ok": False, "provider": self.name, "model": self.model, "error": str(exc)}
        pulled = any(n == self.model or n.split(":")[0] == self.model for n in names)
        return {
            "ok": pulled,
            "provider": self.name,
            "model": self.model,
            "error": None if pulled else f"model not pulled (run: ollama pull {self.model})",
        }
