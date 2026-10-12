"""Offline test double for the LLM. It is NOT a language model.

It lets the whole pipeline (routing, RAG, validation, API, tests, CI) run without Ollama:
 - classification prompts -> returns {"intent": "unknown"} so the rule-based router decides
 - grounded-answer prompts -> returns the best-matching sentences from the numbered context,
   tagged with their [n] marker, or INSUFFICIENT_CONTEXT if nothing overlaps the question.
Evaluation reports produced with this provider are labelled as such.
"""
import json
import re
import time

from app.rag.text import split_sentences, terms
from app.services.llm.base import LLMProvider, LLMResult

_BLOCK_RE = re.compile(r"\[(\d+)\][^\n]*\n(.*?)(?=\n\[\d+\]|\nQuestion:|\Z)", re.S)


class ExtractiveProvider(LLMProvider):
    name = "extractive"

    def __init__(self, model: str = "extractive-baseline"):
        self.model = model

    def chat(self, messages, *, temperature=None, max_tokens=None, json_mode=False) -> LLMResult:
        start = time.perf_counter()
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = messages[-1]["content"]
        if "Classify the customer message" in system:
            text = json.dumps({"intent": "unknown"})
        else:
            text = self._extract(user)
        return LLMResult(text, self.model, (time.perf_counter() - start) * 1000, 0, 0)

    @staticmethod
    def _extract(prompt: str) -> str:
        question = prompt.rsplit("Question:", 1)[-1].strip()
        q_terms = set(terms(question))
        scored = []
        for num, body in _BLOCK_RE.findall(prompt):
            for sentence in split_sentences(body):
                if not sentence.rstrip().endswith((".", "!", "?", ":")) and len(sentence.split()) < 8:
                    continue  # title/heading-like line, not a statement
                overlap = len(q_terms & set(terms(sentence)))
                if overlap:
                    scored.append((overlap, int(num), sentence.strip()))
        if not scored:
            return "INSUFFICIENT_CONTEXT"
        scored.sort(key=lambda s: (-s[0], s[1]))  # more overlap first; ties favour the better-ranked chunk
        best = scored[:2]
        return " ".join(f"{sent} [{num}]" for _, num, sent in best)

    def health(self) -> dict:
        return {"ok": True, "provider": self.name, "model": self.model, "error": None}
