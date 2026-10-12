import pytest

from app.rag.chunking import chunk_pages
from app.rag.loaders import Page, clean_text

MD = """# Refund Policy

## Eligibility
Items must be unused. They must include all accessories.

## Timing
Refunds take 5 to 7 business days.
"""


def test_headings_become_section_path_and_prefix():
    chunks = chunk_pages([Page(MD, None)], chunk_size=500, overlap=50)
    sections = [c.section for c in chunks]
    assert "Refund Policy > Eligibility" in sections and "Refund Policy > Timing" in sections
    assert all(c.text.startswith(c.section) for c in chunks if c.section)


def test_chunks_respect_size_and_never_cross_pages():
    long = " ".join(f"Sentence number {i} is here." for i in range(80))
    chunks = chunk_pages([Page(long, 1), Page("Second page text.", 2)], chunk_size=300, overlap=40)
    assert len(chunks) > 3
    assert all(len(c.text) <= 300 + 80 for c in chunks)  # size + carried overlap
    assert {c.page for c in chunks} == {1, 2}
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert "Second page text." in [c.text for c in chunks if c.page == 2][0]


def test_overlap_repeats_tail_of_previous_chunk():
    long = " ".join(f"Fact{i} is documented." for i in range(60))
    chunks = chunk_pages([Page(long, None)], chunk_size=200, overlap=60)
    first_tail = chunks[0].text.split()[-2:]
    assert all(w in chunks[1].text for w in first_tail)


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        chunk_pages([Page("x", None)], chunk_size=50, overlap=50)


def test_clean_text_normalises_whitespace():
    assert clean_text("a   b\r\n\r\n\r\n\r\nc  ") == "a b\n\nc"


def test_empty_input():
    assert chunk_pages([]) == []
