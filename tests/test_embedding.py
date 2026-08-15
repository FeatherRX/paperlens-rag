from collections.abc import Callable
from math import sqrt

import pytest

from app.chunking import Chunk
from app.embedding import (
    BGE_SMALL_EN_V1_5_DIMENSION,
    BGE_SMALL_EN_V1_5_MODEL_NAME,
    EmbeddedChunk,
    EmbeddingConfig,
    EmbeddingDimensionError,
    EmbeddingEncodingError,
    EmbeddingModelLoadError,
    EmbeddingService,
)


class MockEncoder:
    def __init__(
        self,
        vectors: object,
        *,
        query_vectors: object | None = None,
        error: Exception | None = None,
    ) -> None:
        self.vectors = vectors
        self.query_vectors = vectors if query_vectors is None else query_vectors
        self.error = error
        self.calls: list[dict[str, object]] = []
        self.query_calls: list[str] = []

    def embed(
        self,
        documents: list[str],
        *,
        batch_size: int,
    ) -> object:
        self.calls.append(
            {
                "documents": documents,
                "batch_size": batch_size,
            }
        )

        def generate() -> object:
            if self.error is not None:
                raise self.error
            yield from self.vectors

        return generate()

    def query_embed(self, query: str) -> object:
        self.query_calls.append(query)

        def generate() -> object:
            if self.error is not None:
                raise self.error
            yield from self.query_vectors

        return generate()


def _chunk(
    chunk_index: int,
    text: str,
    *,
    section_title: str | None = None,
    page_numbers: list[int] | None = None,
) -> Chunk:
    return Chunk(
        paper_id="W123",
        chunk_index=chunk_index,
        text=text,
        section_title=section_title,
        page_numbers=page_numbers or [],
    )


def _service(encoder: MockEncoder) -> EmbeddingService:
    return EmbeddingService(
        encoder,
        EmbeddingConfig(expected_dimension=3, batch_size=2),
    )


def test_default_model_contract_uses_bge_small() -> None:
    config = EmbeddingConfig()

    assert config.model_name == "BAAI/bge-small-en-v1.5"
    assert config.model_name == BGE_SMALL_EN_V1_5_MODEL_NAME
    assert config.expected_dimension == 384
    assert config.expected_dimension == BGE_SMALL_EN_V1_5_DIMENSION


def test_embed_chunks_batches_once_preserves_order_and_metadata() -> None:
    encoder = MockEncoder([[3, 4, 0], [0, 0, 2]])
    chunks = [
        _chunk(7, "First", section_title="Methods", page_numbers=[2, 3]),
        _chunk(3, "Second", page_numbers=[4]),
    ]

    results = _service(encoder).embed_chunks(chunks)

    assert encoder.calls == [
        {
            "documents": ["First", "Second"],
            "batch_size": 2,
        }
    ]
    assert [result.chunk_index for result in results] == [7, 3]
    assert results[0].model_dump(exclude={"embedding"}) == {
        "paper_id": "W123",
        "chunk_index": 7,
        "text": "First",
        "section_title": "Methods",
        "page_numbers": [2, 3],
        "model_name": BGE_SMALL_EN_V1_5_MODEL_NAME,
    }
    assert results[0].embedding == pytest.approx([0.6, 0.8, 0.0])
    assert results[1].embedding == pytest.approx([0.0, 0.0, 1.0])
    assert all(isinstance(result, EmbeddedChunk) for result in results)


def test_all_embeddings_are_unit_normalized() -> None:
    results = _service(
        MockEncoder([[1, 2, 3], [-5, 0, 12]])
    ).embed_chunks([_chunk(0, "One"), _chunk(1, "Two")])

    norms = [
        sqrt(sum(value * value for value in result.embedding))
        for result in results
    ]
    assert norms == pytest.approx([1.0, 1.0])


def test_embed_query_uses_query_encoder_and_normalizes() -> None:
    encoder = MockEncoder([], query_vectors=[[3, 4, 0]])

    result = _service(encoder).embed_query("  retrieval augmented generation  ")

    assert encoder.query_calls == ["retrieval augmented generation"]
    assert result == pytest.approx([0.6, 0.8, 0.0])


def test_empty_query_does_not_load_or_call_encoder() -> None:
    load_calls: list[str] = []

    def loader(model_name: str) -> MockEncoder:
        load_calls.append(model_name)
        return MockEncoder([])

    service = EmbeddingService(encoder_loader=loader)

    with pytest.raises(ValueError, match="query must not be empty"):
        service.embed_query("  ")
    assert load_calls == []


def test_query_encoder_failure_has_clear_error() -> None:
    encoder = MockEncoder([], error=RuntimeError("encoder unavailable"))

    with pytest.raises(
        EmbeddingEncodingError,
        match="failed to encode the query",
    ):
        _service(encoder).embed_query("query")


def test_empty_input_does_not_load_or_call_encoder() -> None:
    load_calls: list[str] = []

    def loader(model_name: str) -> MockEncoder:
        load_calls.append(model_name)
        return MockEncoder([])

    service = EmbeddingService(encoder_loader=loader)

    assert service.embed_chunks([]) == []
    assert load_calls == []


def test_model_loading_failure_has_clear_error() -> None:
    def loader(model_name: str) -> MockEncoder:
        raise OSError(f"cannot load {model_name}")

    service = EmbeddingService(
        config=EmbeddingConfig(expected_dimension=3),
        encoder_loader=loader,
    )

    with pytest.raises(
        EmbeddingModelLoadError,
        match="BAAI/bge-small-en-v1.5.*could not be loaded",
    ):
        service.embed_chunks([_chunk(0, "Text")])


@pytest.mark.parametrize(
    ("vectors", "message"),
    [
        ([[1, 0, 0]], "1 embeddings for 2 chunks"),
        ([[1, 0], [0, 1]], "dimension 2; expected 3"),
    ],
)
def test_embedding_count_and_dimensions_are_validated(
    vectors: list[list[int]],
    message: str,
) -> None:
    with pytest.raises(EmbeddingDimensionError, match=message):
        _service(MockEncoder(vectors)).embed_chunks(
            [_chunk(0, "One"), _chunk(1, "Two")]
        )


@pytest.mark.parametrize(
    ("vectors", "message"),
    [
        ([[0, 0, 0]], "could not be normalized"),
        ([[1, float("nan"), 0]], "non-finite"),
        ([[1, "invalid", 0]], "non-numeric"),
    ],
)
def test_unusable_vectors_have_clear_errors(
    vectors: object,
    message: str,
) -> None:
    with pytest.raises(EmbeddingEncodingError, match=message):
        _service(MockEncoder(vectors)).embed_chunks([_chunk(0, "Text")])


def test_encoder_failure_has_clear_error() -> None:
    encoder = MockEncoder([], error=RuntimeError("encoder unavailable"))

    with pytest.raises(
        EmbeddingEncodingError,
        match="failed to encode the chunk batch",
    ):
        _service(encoder).embed_chunks([_chunk(0, "Text")])


@pytest.mark.parametrize(
    "config_factory",
    [
        lambda: EmbeddingConfig(model_name=" "),
        lambda: EmbeddingConfig(expected_dimension=0),
        lambda: EmbeddingConfig(batch_size=0),
    ],
)
def test_invalid_embedding_configuration_is_rejected(
    config_factory: Callable[[], EmbeddingConfig],
) -> None:
    with pytest.raises(ValueError):
        config_factory()
