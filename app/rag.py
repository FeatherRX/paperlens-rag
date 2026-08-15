from collections.abc import Sequence
from typing import Protocol

from app.answer import AnswerService
from app.chunking import ChunkingService
from app.embedding import EmbeddingService
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
    ) -> None:
        self._answer_service = answer_service
        self._store = store or JsonDocumentStore()
        self._chunking_service = chunking_service or ChunkingService()
        self._embedding_service = embedding_service or EmbeddingService()
        self._retrieval_service = retrieval_service or RetrievalService(
            self._embedding_service
        )

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

        chunks = [
            chunk
            for document in documents
            for chunk in self._chunking_service.chunk_document(document)
        ]
        if not chunks:
            raise EmptyRagCorpusError(
                "Selected papers did not contain searchable text"
            )

        embedded_chunks = self._embedding_service.embed_chunks(chunks)
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
