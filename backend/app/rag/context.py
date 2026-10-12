"""Builds the numbered context block that is sent to the LLM, within a character budget."""
from app.rag.retriever import RetrievedChunk


def source_label(chunk: RetrievedChunk) -> str:
    return f"{chunk.filename} — page {chunk.page}" if chunk.page else chunk.filename


def _body(chunk: RetrievedChunk) -> str:
    """Chunks are stored as '<section>\\n<body>' (the section helps retrieval). In the prompt the
    section goes into the label, so the model sees clean prose under a clear heading."""
    if chunk.section and chunk.text.startswith(chunk.section + "\n"):
        return chunk.text[len(chunk.section) + 1 :]
    return chunk.text


def build_context(chunks: list[RetrievedChunk], max_chars: int) -> tuple[str, list[RetrievedChunk]]:
    """Return (context_text, chunks_actually_included). Included order defines the [n] numbers."""
    parts, used, total = [], [], 0
    for chunk in chunks:
        label = source_label(chunk) + (f" · {chunk.section}" if chunk.section else "")
        block = f"[{len(used) + 1}] ({label})\n{_body(chunk)}"
        if used and total + len(block) > max_chars:
            break
        parts.append(block[:max_chars] if not used else block)
        used.append(chunk)
        total += len(block)
    return "\n\n".join(parts), used
