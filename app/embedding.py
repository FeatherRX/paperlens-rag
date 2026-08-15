from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Protocol

from pydantic import BaseModel, Field

from app.chunking import Chunk


BGE_SMALL_EN_V1_5_MODEL_NAME = "BAAI/bge-small-en-v1.5"
BGE_SMALL_EN_V1_5_DIMENSION = 384
DEFAULT_EMBEDDING_BATCH_SIZE = 32


class EmbeddedChunk(BaseModel):
    paper_id: str
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    section_title: str | None = None
    page_numbers: list[int] = Field(default_factory=list)
    embedding: list[float] = Field(min_length=1)
    model_name: str = Field(min_length=1)


@dataclass(frozen=True)
class EmbeddingConfig:
    model_name: str = BGE_SMALL_EN_V1_5_MODEL_NAME
    expected_dimension: int = BGE_SMALL_EN_V1_5_DIMENSION
    batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE

    def __post_init__(self) -> None:
        if not self.model_name.strip():
            raise ValueError("model_name must not be empty")
        if self.expected_dimension <= 0:
            raise ValueError("expected_dimension must be greater than zero")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")


class EmbeddingEncoder(Protocol):
    def embed(
        self,
        documents: list[str],
        *,
        batch_size: int,
    ) -> Iterable[object]: ...

    def query_embed(self, query: str) -> Iterable[object]: ...


class EmbeddingError(RuntimeError):
    """Base error for local embedding generation."""


class EmbeddingModelLoadError(EmbeddingError):
    """Raised when the configured local model cannot be loaded."""


class EmbeddingEncodingError(EmbeddingError):
    """Raised when an encoder cannot produce usable numeric vectors."""


class EmbeddingDimensionError(EmbeddingError):
    """Raised when embedding counts or dimensions violate the contract."""


EncoderLoader = Callable[[str], EmbeddingEncoder]


class EmbeddingService:
    def __init__(
        self,
        encoder: EmbeddingEncoder | None = None,
        config: EmbeddingConfig | None = None,
        encoder_loader: EncoderLoader | None = None,
    ) -> None:
        self.config = config or EmbeddingConfig()
        self._encoder = encoder
        self._encoder_loader = encoder_loader or _load_fastembed

    def embed_chunks(self, chunks: Sequence[Chunk]) -> list[EmbeddedChunk]:
        if not chunks:
            return []

        encoder = self._get_encoder()
        texts = [chunk.text for chunk in chunks]
        try:
            encoded = encoder.embed(
                texts,
                batch_size=self.config.batch_size,
            )
            vectors = _normalized_vectors(
                encoded,
                expected_count=len(chunks),
                expected_dimension=self.config.expected_dimension,
            )
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingEncodingError(
                f"Embedding model '{self.config.model_name}' failed to encode "
                "the chunk batch"
            ) from exc
        return [
            EmbeddedChunk(
                paper_id=chunk.paper_id,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                section_title=chunk.section_title,
                page_numbers=list(chunk.page_numbers),
                embedding=embedding,
                model_name=self.config.model_name,
            )
            for chunk, embedding in zip(chunks, vectors, strict=True)
        ]

    def embed_query(self, query: str) -> list[float]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")

        encoder = self._get_encoder()
        try:
            encoded = encoder.query_embed(normalized_query)
            return _normalized_vectors(
                encoded,
                expected_count=1,
                expected_dimension=self.config.expected_dimension,
            )[0]
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingEncodingError(
                f"Embedding model '{self.config.model_name}' failed to encode "
                "the query"
            ) from exc

    def _get_encoder(self) -> EmbeddingEncoder:
        if self._encoder is not None:
            return self._encoder
        try:
            self._encoder = self._encoder_loader(self.config.model_name)
        except Exception as exc:
            raise EmbeddingModelLoadError(
                f"Embedding model '{self.config.model_name}' could not be loaded"
            ) from exc
        return self._encoder


def _load_fastembed(model_name: str) -> EmbeddingEncoder:
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=model_name)


def _normalized_vectors(
    encoded: object,
    *,
    expected_count: int,
    expected_dimension: int,
) -> list[list[float]]:
    rows = _as_rows(encoded)
    if len(rows) != expected_count:
        raise EmbeddingDimensionError(
            f"Encoder returned {len(rows)} embeddings for {expected_count} chunks"
        )

    vectors: list[list[float]] = []
    for index, row in enumerate(rows):
        values = _as_sequence(row)
        if len(values) != expected_dimension:
            raise EmbeddingDimensionError(
                f"Embedding {index} has dimension {len(values)}; expected "
                f"{expected_dimension}"
            )
        try:
            vector = [float(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise EmbeddingEncodingError(
                f"Embedding {index} contained a non-numeric value"
            ) from exc
        if not all(isfinite(value) for value in vector):
            raise EmbeddingEncodingError(
                f"Embedding {index} contained a non-finite value"
            )

        norm = sqrt(sum(value * value for value in vector))
        if not isfinite(norm) or norm == 0:
            raise EmbeddingEncodingError(
                f"Embedding {index} could not be normalized"
            )
        vectors.append([value / norm for value in vector])
    return vectors


def _as_rows(value: object) -> list[object]:
    if isinstance(value, Iterable) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return list(value)
    raise EmbeddingEncodingError("Encoder output was not an embedding iterable")


def _as_sequence(value: object) -> Sequence[object]:
    to_list = getattr(value, "tolist", None)
    if callable(to_list):
        value = to_list()
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return value
    raise EmbeddingEncodingError("Encoder output was not a vector sequence")
