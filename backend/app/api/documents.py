import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_container, get_session, require_admin
from app.models.db import Document
from app.rag.embeddings import EmbeddingError
from app.rag.ingestion import delete_document, ingest_file
from app.rag.loaders import SUPPORTED_EXTENSIONS
from app.services.cache import bump_kb_version
from app.services.container import Container

router = APIRouter(prefix="/api/documents", tags=["knowledge base"], dependencies=[Depends(require_admin)])


def _doc(d: Document) -> dict:
    return {"id": d.id, "filename": d.filename, "source": d.source, "pages": d.num_pages, "chunks": d.num_chunks,
            "size_bytes": d.size_bytes, "embedding_model": d.embedding_model, "uploaded_at": d.uploaded_at}


@router.get("")
def list_documents(session: Session = Depends(get_session)):
    return [_doc(d) for d in session.scalars(select(Document).order_by(Document.uploaded_at.desc()))]


@router.post("/upload", status_code=201)
async def upload_document(file: UploadFile, c: Container = Depends(get_container), session: Session = Depends(get_session)):
    name = Path(file.filename or "").name  # strips any directory components
    ext = Path(name).suffix.lower()
    if not name or ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(415, f"Unsupported file type. Allowed: {sorted(SUPPORTED_EXTENSIONS)}")
    max_bytes = c.settings.max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, f"File larger than {c.settings.max_upload_mb} MB.")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{ext}"  # never trust the client's file name for the path
        path.write_bytes(data)
        try:
            doc = ingest_file(session, c.store, c.embedder, path, filename=name, source="upload",
                              chunk_size=c.settings.chunk_size, chunk_overlap=c.settings.chunk_overlap)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except EmbeddingError as exc:
            raise HTTPException(503, f"Embedding model unavailable: {exc}") from exc
        except Exception as exc:  # corrupt PDF/DOCX etc.
            raise HTTPException(422, f"Could not read document: {type(exc).__name__}") from exc
    bump_kb_version(c.cache)  # invalidates cached knowledge answers
    return _doc(doc)


@router.delete("/{document_id}", status_code=204)
def remove_document(document_id: str, c: Container = Depends(get_container), session: Session = Depends(get_session)):
    if not delete_document(session, c.store, document_id):
        raise HTTPException(404, "Document not found.")
    bump_kb_version(c.cache)
