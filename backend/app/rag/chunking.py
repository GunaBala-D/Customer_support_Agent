"""Structure-aware chunking.

- Splits on blank lines (paragraphs), packs paragraphs up to `chunk_size` characters.
- Markdown headings update a section path ("Refund Policy > Eligibility") that is prepended to
  each chunk, so a chunk like "30 days" still knows which policy it belongs to.
- Chunks never cross page boundaries, so page citations stay accurate.
- Consecutive chunks share a small overlap so facts split across a boundary are still retrievable.
"""
import re
from dataclasses import dataclass

from app.rag.loaders import Page
from app.rag.text import split_sentences

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


@dataclass
class Chunk:
    text: str
    page: int | None
    index: int
    section: str | None


def _tail_overlap(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    cut = tail.find(" ")
    return tail[cut + 1 :] if cut != -1 else tail


def _split_long(paragraph: str, size: int) -> list[str]:
    if len(paragraph) <= size:
        return [paragraph]
    parts, buf = [], ""
    for sent in split_sentences(paragraph):
        while len(sent) > size:  # pathological sentence without punctuation
            parts.append(sent[:size])
            sent = sent[size:]
        if buf and len(buf) + len(sent) + 1 > size:
            parts.append(buf)
            buf = sent
        else:
            buf = f"{buf} {sent}".strip()
    if buf:
        parts.append(buf)
    return parts


def _section_blocks(text: str, headings: list[tuple[int, str]]) -> list[tuple[str | None, str]]:
    """Split page text into (section path, paragraph) blocks. `headings` (the section stack) is
    updated in place so it carries across pages."""
    blocks: list[tuple[str | None, str]] = []

    def path() -> str | None:
        return " > ".join(h for _, h in headings) or None

    for raw in _PARAGRAPH_SPLIT_RE.split(text):
        body_lines: list[str] = []
        for line in raw.strip().split("\n"):
            m = _HEADING_RE.match(line)
            if m:
                if body_lines:
                    blocks.append((path(), "\n".join(body_lines)))
                    body_lines = []
                level = len(m.group(1))
                headings[:] = [(lv, h) for lv, h in headings if lv < level] + [(level, m.group(2))]
            elif line.strip() or body_lines:
                body_lines.append(line)
        if body_lines:
            blocks.append((path(), "\n".join(body_lines)))
    return blocks


def _pack(blocks: list[tuple[str | None, str]], page: int | None, size: int, overlap: int, start: int) -> list[Chunk]:
    out: list[Chunk] = []
    buf, buf_section = "", None

    def emit() -> None:
        body = buf.strip()
        if body:
            text = f"{buf_section}\n{body}" if buf_section else body
            out.append(Chunk(text, page, start + len(out), buf_section))

    for section, para in blocks:
        for piece in _split_long(para, size):
            if buf and (section != buf_section or len(buf) + len(piece) + 2 > size):
                emit()
                buf = _tail_overlap(buf.strip(), overlap) if section == buf_section else ""
            if not buf:
                buf_section = section
            buf = f"{buf}\n\n{piece}".strip() if buf else piece
    emit()
    return out


def chunk_pages(pages: list[Page], chunk_size: int = 800, overlap: int = 120) -> list[Chunk]:
    if chunk_size <= overlap:
        raise ValueError("chunk_size must be larger than chunk_overlap")
    chunks: list[Chunk] = []
    headings: list[tuple[int, str]] = []  # section stack carried across pages
    for page in pages:
        blocks = _section_blocks(page.text, headings)
        chunks.extend(_pack(blocks, page.page, chunk_size, overlap, len(chunks)))
    return chunks
