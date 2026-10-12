"""Ingestion: file -> pages -> chunks -> embeddings -> vector store + document tables."""
import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db import Document, DocumentChunk
from app.rag.chunking import chunk_pages
from app.rag.embeddings import EmbeddingProvider
from app.rag.loaders import load_document
from app.rag.vectorstore import VectorStore


def ingest_file(
    session: Session,
    store: VectorStore,
    embedder: EmbeddingProvider,
    path: Path,
    *,
    filename: str | None = None,
    source: str = "upload",
    chunk_size: int = 800,
    chunk_overlap: int = 120,
) -> Document:
    """Ingest one file. Re-uploading the same filename replaces the old version;
    identical content is a no-op and returns the existing document."""
    filename = filename or path.name
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()

    existing = session.scalars(select(Document).where(Document.filename == filename)).all()
    for doc in existing:
        if doc.content_hash == digest and doc.embedding_model == embedder.model:
            return doc
    for doc in existing:
        delete_document(session, store, doc.id)

    pages = load_document(path)
    chunks = chunk_pages(pages, chunk_size, chunk_overlap)
    if not chunks:
        raise ValueError("Document contains no extractable text")

    doc_id = str(uuid.uuid4())
    ids = [f"{doc_id}:{c.index}" for c in chunks]
    # Embed first: if the embedding backend fails nothing is written anywhere.
    vectors = embedder.embed_documents([c.text for c in chunks])
    doc = Document(
        id=doc_id,
        filename=filename,
        source=source,
        content_hash=digest,
        size_bytes=len(raw),
        num_pages=max((p.page or 0) for p in pages) or None,
        num_chunks=len(chunks),
        embedding_model=embedder.model,
    )
    session.add(doc)
    session.add_all(
        DocumentChunk(id=i, document_id=doc_id, chunk_index=c.index, page=c.page, section=c.section, text=c.text)
        for i, c in zip(ids, chunks, strict=True)
    )
    store.add(
        ids,
        vectors,
        [c.text for c in chunks],
        [
            {"document_id": doc_id, "filename": filename, "page": c.page, "section": c.section,
             "chunk_index": c.index, "source": source}
            for c in chunks
        ],
    )
    session.commit()
    return doc


def delete_document(session: Session, store: VectorStore, document_id: str) -> bool:
    doc = session.get(Document, document_id)
    if doc is None:
        return False
    store.delete_document(document_id)
    session.delete(doc)
    session.commit()
    return True


def ingest_directory(session, store, embedder, directory: Path, **kwargs) -> list[Document]:
    from app.rag.loaders import SUPPORTED_EXTENSIONS

    docs = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            docs.append(ingest_file(session, store, embedder, path, source="bootstrap", **kwargs))
    return docs


def index_is_stale(session: Session, store: VectorStore) -> bool:
    """True when the vector index doesn't match the chunk rows in the database (new database, new embedding model,
    or a wiped vector folder). The chunk text is stored in SQL, so the index can always be rebuilt from it."""
    from sqlalchemy import func

    return store.count() != (session.scalar(select(func.count()).select_from(DocumentChunk)) or 0)


def reindex_all(session: Session, store: VectorStore, embedder: EmbeddingProvider) -> dict:
    """Re-embed every stored chunk with the current embedding model. Embeds first, so a failing embedding
    backend leaves the existing index untouched."""
    chunks = session.scalars(select(DocumentChunk).order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)).all()
    docs = {d.id: d for d in session.scalars(select(Document))}
    vectors = embedder.embed_documents([c.text for c in chunks]) if chunks else []
    store.reset()
    if chunks:
        store.add(
            [c.id for c in chunks], vectors, [c.text for c in chunks],
            [{"document_id": c.document_id, "filename": docs[c.document_id].filename, "page": c.page, "section": c.section,
              "chunk_index": c.chunk_index, "source": docs[c.document_id].source} for c in chunks],
        )
    for d in docs.values():
        d.embedding_model = embedder.model
    session.commit()
    return {"documents": len(docs), "chunks": len(chunks), "embedding_model": embedder.model}
