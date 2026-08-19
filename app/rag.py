from collections.abc import Sequence
from typing import Protocol

from app.answer import AnswerService
from app.chunking import Chunk, ChunkingService
from app.corpus_cache import (
    CORPUS_CACHE_DIRECTORY_NAME,
    CorpusEmbeddingCacheStore,
)
from app.embedding import EmbeddedChunk, EmbeddingService
from app.ingestion import JsonDocumentStore, NormalizedDocument
from app.models import RagAnswerResponse, RagCitation
from app.retrieval import RetrievalResult, RetrievalService


class DocumentStore(Protocol):
    def load(self, paper_id: str) -> NormalizedDocument | None: ...


class RagServiceError(RuntimeError):
    """Base error for the local RAG answer pipeline."""


class IngestedPapersNotFoundError(RagServiceError):
    def __init__(self, paper_ids: list[str]) -> None:
        super().__init__("One or more requested papers have not been ingested")
        self.paper_ids = paper_ids


class EmptyRagCorpusError(RagServiceError):
    """Raised when selected documents do not produce searchable chunks."""


class RagAnswerService:
    def __init__(
        self,
        answer_service: AnswerService,
        *,
        store: DocumentStore | None = None,
        chunking_service: ChunkingService | None = None,
        embedding_service: EmbeddingService | None = None,
        retrieval_service: RetrievalService | None = None,
        corpus_cache: CorpusEmbeddingCacheStore | None = None,
    ) -> None:
        self._answer_service = answer_service
        self._store = store or JsonDocumentStore()
        self._chunking_service = chunking_service or ChunkingService()
        self._embedding_service = embedding_service or EmbeddingService()
        self._retrieval_service = retrieval_service or RetrievalService(
            self._embedding_service
        )
        if corpus_cache is not None:
            self._corpus_cache = corpus_cache
        elif isinstance(self._store, JsonDocumentStore):
            self._corpus_cache = CorpusEmbeddingCacheStore(
                self._store.root / CORPUS_CACHE_DIRECTORY_NAME
            )
        else:
            self._corpus_cache = None

    def generate_answer(
        self,
        query: str,
        paper_ids: Sequence[str],
        *,
        top_k: int,
    ) -> RagAnswerResponse:
        documents, missing_ids = self._load_documents(paper_ids)
        if missing_ids:
            raise IngestedPapersNotFoundError(missing_ids)

        embedded_chunks = self._embedded_corpus(documents)
        if not embedded_chunks:
            raise EmptyRagCorpusError(
                "Selected papers did not contain searchable text"
            )

        evidence = self._retrieval_service.retrieve(
            query,
            embedded_chunks,
            top_k=top_k,
        )
        if not evidence:
            raise EmptyRagCorpusError(
                "Selected papers did not produce retrieval evidence"
            )

        answer = self._answer_service.generate_answer(query, evidence)
        titles = {document.paper_id: document.title for document in documents}
        return RagAnswerResponse(
            answer=answer.answer,
            citations=_citations(evidence, titles),
        )

    def _load_documents(
        self,
        paper_ids: Sequence[str],
    ) -> tuple[list[NormalizedDocument], list[str]]:
        documents: list[NormalizedDocument] = []
        missing_ids: list[str] = []
        for paper_id in paper_ids:
            document = self._store.load(paper_id)
            if document is None:
                missing_ids.append(paper_id)
            else:
                documents.append(document)
        return documents, missing_ids

    def _embedded_corpus(
        self,
        documents: Sequence[NormalizedDocument],
    ) -> list[EmbeddedChunk]:
        corpus_by_document: list[list[EmbeddedChunk] | None] = []
        pending_documents: list[
            tuple[int, NormalizedDocument, list[Chunk]]
        ] = []
        chunks_to_embed: list[Chunk] = []

        for document in documents:
            cached_chunks = (
                self._corpus_cache.load(
                    document,
                    chunking_config=self._chunking_service.config,
                    embedding_config=self._embedding_service.config,
                )
                if self._corpus_cache is not None
                else None
            )
            corpus_by_document.append(cached_chunks)
            if cached_chunks is not None:
                continue

            document_chunks = self._chunking_service.chunk_document(document)
            document_index = len(corpus_by_document) - 1
            pending_documents.append(
                (document_index, document, document_chunks)
            )
            chunks_to_embed.extend(document_chunks)

        embedded_misses = self._embedding_service.embed_chunks(chunks_to_embed)
        offset = 0
        for document_index, document, document_chunks in pending_documents:
            end = offset + len(document_chunks)
            document_embeddings = embedded_misses[offset:end]
            offset = end
            corpus_by_document[document_index] = document_embeddings
            if self._corpus_cache is not None:
                self._corpus_cache.save(
                    document,
                    document_embeddings,
                    chunking_config=self._chunking_service.config,
                    embedding_config=self._embedding_service.config,
                )

        return [
            chunk
            for document_chunks in corpus_by_document
            for chunk in (document_chunks or [])
        ]


def _citations(
    evidence: Sequence[RetrievalResult],
    titles: dict[str, str | None],
) -> list[RagCitation]:
    return [
        RagCitation(
            citation_number=number,
            paper_id=result.paper_id,
            paper_title=titles.get(result.paper_id),
            chunk_index=result.chunk_index,
            page_numbers=list(result.page_numbers),
            section_title=result.section_title,
            evidence_excerpt=result.text,
            retrieval_score=result.score,
        )
        for number, result in enumerate(evidence, start=1)
    ]
