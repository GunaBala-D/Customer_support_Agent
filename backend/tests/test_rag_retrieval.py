import shutil

from app.models.db import Document, DocumentChunk
from app.rag.embeddings import HashEmbeddings
from app.rag.ingestion import delete_document, ingest_file
from app.rag.pipeline import citations_from_answer
from app.rag.retriever import RetrievedChunk
from app.rag.vectorstore import ChromaVectorStore


def test_relevant_chunk_retrieved_with_page_metadata(container):
    res = container.rag.retrieve("How long does standard shipping take?")
    top = res.relevant[0]
    assert top.filename == "shipping_policy.pdf" and top.page == 1
    assert "3 to 5 business days" in top.text


def test_off_topic_question_has_no_relevant_evidence(container):
    res = container.rag.retrieve("Who won the football world cup in 2018?")
    assert res.relevant == []
    assert res.top_score is not None  # candidates exist, but none cleared the threshold


def test_each_document_format_is_indexed(session):
    names = {d.filename for d in session.query(Document)}
    assert {"refund_policy.md", "cancellation_policy.txt", "shipping_policy.pdf", "warranty_policy.docx"} <= names


def test_chunk_rows_match_vector_store(session, container):
    assert session.query(DocumentChunk).count() == container.store.count()


def test_scores_are_blend_of_vector_and_lexical(container):
    for c in container.rag.retrieve("refund window").ranked:
        assert abs(c.score - round(0.5 * c.vector_score + 0.5 * c.lexical_score, 4)) < 1e-3


def _chunk(i, fn="a.pdf", page=2):
    return RetrievedChunk(f"c{i}", "d", fn, page, "Sec", f"text {i}", 0.5, 0.5, 0.5)


def test_citations_map_markers_to_real_chunks_only():
    used = [_chunk(1), _chunk(2, "b.md", None)]
    cites, mode = citations_from_answer("Yes [2]. Also see [7].", used)  # [7] does not exist
    assert mode == "marker" and [c["filename"] for c in cites] == ["b.md"]
    assert cites[0]["label"] == "b.md"
    cites, mode = citations_from_answer("Answer without markers", used)
    assert mode == "retrieval_fallback" and cites[0]["label"] == "a.pdf — page 2"
    assert citations_from_answer("x", []) == ([], "none")


def test_reupload_same_content_is_noop_and_changed_content_replaces(tmp_path, container, session):
    f = tmp_path / "promo_terms.txt"
    f.write_text("Promo codes expire after 14 days. Only one code per order.", encoding="utf-8")
    d1 = ingest_file(session, container.store, container.embedder, f)
    assert ingest_file(session, container.store, container.embedder, f).id == d1.id
    before = container.store.count()
    f.write_text("Promo codes expire after 21 days. Only one code per order.", encoding="utf-8")
    d2 = ingest_file(session, container.store, container.embedder, f)
    assert d2.id != d1.id and container.store.count() == before
    assert session.get(Document, d1.id) is None
    assert delete_document(session, container.store, d2.id)
    assert container.store.count() == before - 1


def test_vector_store_isolated_per_embedding_model(tmp_path):
    a = ChromaVectorStore(str(tmp_path), "kb_model_a")
    b = ChromaVectorStore(str(tmp_path), "kb_model_b")
    emb = HashEmbeddings(16)
    a.add(["1"], emb.embed_documents(["alpha"]), ["alpha"], [{"document_id": "d"}])
    assert a.count() == 1 and b.count() == 0
    assert b.query(emb.embed_query("alpha"), 3) == []
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_marker_less_answer_is_attributed_to_the_chunk_that_supports_it():
    a = RetrievedChunk("c1", "d", "warranty.docx", None, "Claims", "Send the serial number and photos to make a claim.", 0.5, 0.5, 0.5)
    b = RetrievedChunk("c2", "d", "refund_policy.md", None, "Returns", "Returns are accepted within 30 days of delivery.", 0.5, 0.5, 0.5)
    cites, mode = citations_from_answer("Returns are accepted within 30 days.", [a, b])
    assert mode == "retrieval_fallback" and cites[0]["filename"] == "refund_policy.md"
