from collections.abc import Sequence
from dataclasses import dataclass
from math import isclose, isfinite, sqrt

from pydantic import BaseModel, Field

from app.embedding import EmbeddedChunk, EmbeddingService


DEFAULT_TOP_K = 5
UNIT_VECTOR_TOLERANCE = 1e-5


class RetrievalResult(BaseModel):
    rank: int = Field(ge=1)
    score: float
    paper_id: str
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    section_title: str | None = None
    page_numbers: list[int] = Field(default_factory=list)


@dataclass(frozen=True)
class RetrievalConfig:
    top_k: int = DEFAULT_TOP_K

    def __post_init__(self) -> None:
        _validate_top_k(self.top_k)


class RetrievalError(RuntimeError):
    """Base error for in-memory chunk retrieval."""


class RetrievalVectorError(RetrievalError):
    """Raised when a query or corpus vector violates the retrieval contract."""


class RetrievalService:
    def __init__(
        self,
        embedding_service: EmbeddingService,
        config: RetrievalConfig | None = None,
    ) -> None:
        self.embedding_service = embedding_service
        self.config = config or RetrievalConfig()

    def retrieve(
        self,
        query: str,
        corpus: Sequence[EmbeddedChunk],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        result_limit = self.config.top_k if top_k is None else top_k
        _validate_top_k(result_limit)
        if not corpus:
            return []

        query_vector = self.embedding_service.embed_query(query)
        expected_dimension = len(query_vector)
        _validate_vector(
            query_vector,
            expected_dimension=expected_dimension,
            label="Query embedding",
        )

        scored_chunks: list[tuple[float, str, int, int, EmbeddedChunk]] = []
        for corpus_position, chunk in enumerate(corpus):
            _validate_vector(
                chunk.embedding,
                expected_dimension=expected_dimension,
                label=(
                    f"Embedding for paper '{chunk.paper_id}' chunk "
                    f"{chunk.chunk_index}"
                ),
            )
            score = sum(
                query_value * chunk_value
                for query_value, chunk_value in zip(
                    query_vector,
                    chunk.embedding,
                    strict=True,
                )
            )
            if not isfinite(score):
                raise RetrievalVectorError(
                    f"Similarity score for paper '{chunk.paper_id}' chunk "
                    f"{chunk.chunk_index} was non-finite"
                )
            scored_chunks.append(
                (
                    score,
                    chunk.paper_id,
                    chunk.chunk_index,
                    corpus_position,
                    chunk,
                )
            )

        scored_chunks.sort(
            key=lambda item: (-item[0], item[1], item[2], item[3])
        )
        return [
            RetrievalResult(
                rank=rank,
                score=score,
                paper_id=chunk.paper_id,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                section_title=chunk.section_title,
                page_numbers=list(chunk.page_numbers),
            )
            for rank, (score, _, _, _, chunk) in enumerate(
                scored_chunks[:result_limit],
                start=1,
            )
        ]


def _validate_top_k(top_k: object) -> None:
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("top_k must be a positive integer")


def _validate_vector(
    vector: Sequence[float],
    *,
    expected_dimension: int,
    label: str,
) -> None:
    if expected_dimension <= 0 or len(vector) != expected_dimension:
        raise RetrievalVectorError(
            f"{label} has dimension {len(vector)}; expected "
            f"{expected_dimension}"
        )
    if not all(isfinite(value) for value in vector):
        raise RetrievalVectorError(f"{label} contains a non-finite value")

    norm = sqrt(sum(value * value for value in vector))
    if not isfinite(norm) or not isclose(
        norm,
        1.0,
        rel_tol=UNIT_VECTOR_TOLERANCE,
        abs_tol=UNIT_VECTOR_TOLERANCE,
    ):
        raise RetrievalVectorError(f"{label} must be L2-normalized")
