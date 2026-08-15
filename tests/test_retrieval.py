from collections.abc import Iterable
from math import sqrt

import pytest

from app.embedding import EmbeddedChunk, EmbeddingConfig, EmbeddingService
from app.retrieval import (
    RetrievalConfig,
    RetrievalResult,
    RetrievalService,
    RetrievalVectorError,
)


class QueryEncoder:
    def __init__(self, query_vector: list[float]) -> None:
        self.query_vector = query_vector
        self.query_calls: list[str] = []

    def embed(
        self,
        documents: list[str],
        *,
        batch_size: int,
    ) -> Iterable[object]:
        raise AssertionError("document embedding is not used during retrieval")

    def query_embed(self, query: str) -> Iterable[object]:
        self.query_calls.append(query)
        yield self.query_vector


def _embedded_chunk(
    paper_id: str,
    chunk_index: int,
    embedding: list[float],
    *,
    text: str | None = None,
    section_title: str | None = None,
    page_numbers: list[int] | None = None,
) -> EmbeddedChunk:
    return EmbeddedChunk(
        paper_id=paper_id,
        chunk_index=chunk_index,
        text=text or f"Text for {paper_id}/{chunk_index}",
        section_title=section_title,
        page_numbers=page_numbers or [],
        embedding=embedding,
        model_name="test-model",
    )


def _retrieval_service(
    query_vector: list[float] | None = None,
    *,
    top_k: int = 5,
) -> tuple[RetrievalService, QueryEncoder]:
    encoder = QueryEncoder(query_vector or [1.0, 0.0, 0.0])
    embedding_service = EmbeddingService(
        encoder=encoder,
        config=EmbeddingConfig(
            model_name="test-model",
            expected_dimension=3,
            batch_size=2,
        ),
    )
    return (
        RetrievalService(
            embedding_service,
            RetrievalConfig(top_k=top_k),
        ),
        encoder,
    )


def test_retrieve_ranks_top_k_across_papers_and_preserves_metadata() -> None:
    service, encoder = _retrieval_service(top_k=2)
    corpus = [
        _embedded_chunk("W200", 4, [0.6, 0.8, 0.0]),
        _embedded_chunk(
            "W100",
            2,
            [1.0, 0.0, 0.0],
            text="Most relevant evidence",
            section_title="Results",
            page_numbers=[7, 8],
        ),
        _embedded_chunk("W300", 1, [0.0, 1.0, 0.0]),
    ]

    results = service.retrieve("  graph retrieval  ", corpus)

    assert encoder.query_calls == ["graph retrieval"]
    assert [result.rank for result in results] == [1, 2]
    assert [result.paper_id for result in results] == ["W100", "W200"]
    assert [result.score for result in results] == pytest.approx([1.0, 0.6])
    assert results[0].model_dump() == {
        "rank": 1,
        "score": 1.0,
        "paper_id": "W100",
        "chunk_index": 2,
        "text": "Most relevant evidence",
        "section_title": "Results",
        "page_numbers": [7, 8],
    }
    assert all(isinstance(result, RetrievalResult) for result in results)


def test_top_k_can_override_config_and_is_limited_by_corpus_size() -> None:
    service, _ = _retrieval_service(top_k=1)
    corpus = [
        _embedded_chunk("W1", 0, [1.0, 0.0, 0.0]),
        _embedded_chunk("W2", 0, [0.0, 1.0, 0.0]),
    ]

    results = service.retrieve("query", corpus, top_k=10)

    assert len(results) == 2
    assert [result.rank for result in results] == [1, 2]


def test_equal_scores_have_deterministic_paper_and_chunk_tiebreakers() -> None:
    service, _ = _retrieval_service()
    tied_vector = [1 / sqrt(2), 1 / sqrt(2), 0.0]
    corpus = [
        _embedded_chunk("W2", 0, tied_vector),
        _embedded_chunk("W1", 2, tied_vector),
        _embedded_chunk("W1", 1, tied_vector),
    ]

    first = service.retrieve("query", corpus)
    second = service.retrieve("query", corpus)

    expected_order = [("W1", 1), ("W1", 2), ("W2", 0)]
    assert [(item.paper_id, item.chunk_index) for item in first] == expected_order
    assert first == second


def test_empty_corpus_returns_empty_without_embedding_query() -> None:
    service, encoder = _retrieval_service()

    assert service.retrieve("query", []) == []
    assert encoder.query_calls == []


@pytest.mark.parametrize("top_k", [0, -1, True, 1.5])
def test_invalid_top_k_is_rejected(top_k: object) -> None:
    with pytest.raises(ValueError, match="top_k must be a positive integer"):
        RetrievalConfig(top_k=top_k)  # type: ignore[arg-type]


def test_invalid_top_k_override_is_rejected_for_empty_corpus() -> None:
    service, _ = _retrieval_service()

    with pytest.raises(ValueError, match="top_k must be a positive integer"):
        service.retrieve("query", [], top_k=0)


def test_corpus_vector_dimension_must_match_query() -> None:
    service, _ = _retrieval_service()
    corpus = [_embedded_chunk("W1", 0, [1.0, 0.0])]

    with pytest.raises(
        RetrievalVectorError,
        match="dimension 2; expected 3",
    ):
        service.retrieve("query", corpus)


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf")])
def test_corpus_vector_values_must_be_finite(invalid_value: float) -> None:
    service, _ = _retrieval_service()
    corpus = [_embedded_chunk("W1", 0, [invalid_value, 0.0, 0.0])]

    with pytest.raises(RetrievalVectorError, match="non-finite value"):
        service.retrieve("query", corpus)


def test_corpus_vectors_must_be_l2_normalized() -> None:
    service, _ = _retrieval_service()
    corpus = [_embedded_chunk("W1", 0, [2.0, 0.0, 0.0])]

    with pytest.raises(RetrievalVectorError, match="must be L2-normalized"):
        service.retrieve("query", corpus)


def test_empty_query_is_rejected_for_nonempty_corpus() -> None:
    service, _ = _retrieval_service()
    corpus = [_embedded_chunk("W1", 0, [1.0, 0.0, 0.0])]

    with pytest.raises(ValueError, match="query must not be empty"):
        service.retrieve("   ", corpus)
