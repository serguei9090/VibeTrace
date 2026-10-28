"""Documentation RAG subsystem for VibeTrace."""

from __future__ import annotations

from vibetrace.docs.chunker import HeadingChunk, chunk_markdown, chunk_pdf, chunk_text
from vibetrace.docs.db import DocDB, get_global_doc_db, get_local_doc_db
from vibetrace.docs.schemas import DocCatalogInfo, DocChunkItem, DocSearchQuery
from vibetrace.docs.service import DocCatalogManager, DocIndexService, DocSearchService

__all__ = [
    "DocCatalogInfo",
    "DocCatalogManager",
    "DocChunkItem",
    "DocDB",
    "DocIndexService",
    "DocSearchQuery",
    "DocSearchService",
    "HeadingChunk",
    "chunk_markdown",
    "chunk_pdf",
    "chunk_text",
    "get_global_doc_db",
    "get_local_doc_db",
]
