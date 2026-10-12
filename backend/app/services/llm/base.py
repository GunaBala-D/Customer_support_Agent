"""Provider-agnostic LLM interface. The rest of the app only imports from here."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


class LLMError(Exception):
    """Raised when the model backend is unreachable or returns an unusable response."""


@dataclass
class LLMResult:
    text: str
    model: str
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResult: ...

    @abstractmethod
    def health(self) -> dict: ...
