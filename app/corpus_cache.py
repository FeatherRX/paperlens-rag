import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.chunking import ChunkingConfig
from app.embedding import EmbeddedChunk, EmbeddingConfig
from app.ingestion import DEFAULT_INGESTION_DIRECTORY, NormalizedDocument


CORPUS_CACHE_SCHEMA_VERSION = 1
CHUNKING_ALGORITHM_VERSION = "ordered-character-window-v1"
EMBEDDING_PIPELINE_VERSION = "fastembed-l2-v1"
EMBEDDING_NORMALIZATION = "l2"
DEFAULT_CORPUS_CACHE_DIRECTORY = (
    DEFAULT_INGESTION_DIRECTORY / ".corpus-embeddings"
)
CORPUS_CACHE_DIRECTORY_NAME = ".corpus-embeddings"
UNIT_VECTOR_TOLERANCE = 1e-5
OPENALEX_WORK_ID_PATTERN = re.compile(r"^W[1-9]\d*$")


class ChunkingCacheSignature(BaseModel):
    algorithm_version: str = Field(min_length=1)
    max_characters: int = Field(gt=0)
    overlap_characters: int = Field(ge=0)


class EmbeddingCacheSignature(BaseModel):
    pipeline_version: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    dimension: int = Field(gt=0)
    normalization: Literal["l2"] = "l2"


class CorpusEmbeddingCacheEnvelope(BaseModel):
    cache_schema_version: int
    paper_id: str = Field(min_length=1)
    normalized_document_fingerprint: str = Field(
        min_length=64,
        max_length=64,
    )
    document_schema_version: int
    chunking: ChunkingCacheSignature
    embedding: EmbeddingCacheSignature
    chunks_fingerprint: str = Field(min_length=64, max_length=64)
    chunks: list[EmbeddedChunk]


class CorpusEmbeddingCacheStore:
    def __init__(
        self,
        root: Path = DEFAULT_CORPUS_CACHE_DIRECTORY,
        *,
        chunking_algorithm_version: str = CHUNKING_ALGORITHM_VERSION,
        embedding_pipeline_version: str = EMBEDDING_PIPELINE_VERSION,
    ) -> None:
        self.root = root
        self.chunking_algorithm_version = chunking_algorithm_version
        self.embedding_pipeline_version = embedding_pipeline_version

    def load(
        self,
        document: NormalizedDocument,
        *,
        chunking_config: ChunkingConfig,
        embedding_config: EmbeddingConfig,
    ) -> list[EmbeddedChunk] | None:
        path = self._path(document.paper_id)
        if path is None:
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            envelope = CorpusEmbeddingCacheEnvelope.model_validate(payload)
        except (OSError, ValueError, ValidationError):
            return None

        expected_chunking = self._chunking_signature(chunking_config)
        expected_embedding = self._embedding_signature(embedding_config)
        if (
            envelope.cache_schema_version != CORPUS_CACHE_SCHEMA_VERSION
            or envelope.paper_id != document.paper_id
            or envelope.document_schema_version != document.schema_version
            or envelope.normalized_document_fingerprint
            != normalized_document_fingerprint(document)
            or envelope.chunking != expected_chunking
            or envelope.embedding != expected_embedding
            or not _valid_cached_chunks(
                envelope.chunks,
                paper_id=document.paper_id,
                embedding_config=embedding_config,
            )
            or envelope.chunks_fingerprint
            != embedded_chunks_fingerprint(envelope.chunks)
        ):
            return None
        return [chunk.model_copy(deep=True) for chunk in envelope.chunks]

    def save(
        self,
        document: NormalizedDocument,
        chunks: list[EmbeddedChunk],
        *,
        chunking_config: ChunkingConfig,
        embedding_config: EmbeddingConfig,
    ) -> bool:
        path = self._path(document.paper_id)
        if path is None or not _valid_cached_chunks(
            chunks,
            paper_id=document.paper_id,
            embedding_config=embedding_config,
        ):
            return False

        envelope = CorpusEmbeddingCacheEnvelope(
            cache_schema_version=CORPUS_CACHE_SCHEMA_VERSION,
            paper_id=document.paper_id,
            normalized_document_fingerprint=normalized_document_fingerprint(
                document
            ),
            document_schema_version=document.schema_version,
            chunking=self._chunking_signature(chunking_config),
            embedding=self._embedding_signature(embedding_config),
            chunks_fingerprint=embedded_chunks_fingerprint(chunks),
            chunks=chunks,
        )

        temporary_path: Path | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.root,
                prefix=f".{document.paper_id}-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(
                    envelope.model_dump(mode="json"),
                    temporary_file,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
            return True
        except (OSError, TypeError, ValueError):
            return False
        finally:
            if temporary_path is not None and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    def _path(self, paper_id: str) -> Path | None:
        if OPENALEX_WORK_ID_PATTERN.fullmatch(paper_id) is None:
            return None
        return self.root / f"{paper_id}.json"

    def _chunking_signature(
        self,
        config: ChunkingConfig,
    ) -> ChunkingCacheSignature:
        return ChunkingCacheSignature(
            algorithm_version=self.chunking_algorithm_version,
            max_characters=config.max_characters,
            overlap_characters=config.overlap_characters,
        )

    def _embedding_signature(
        self,
        config: EmbeddingConfig,
    ) -> EmbeddingCacheSignature:
        return EmbeddingCacheSignature(
            pipeline_version=self.embedding_pipeline_version,
            model_name=config.model_name,
            dimension=config.expected_dimension,
            normalization=EMBEDDING_NORMALIZATION,
        )


def normalized_document_fingerprint(document: NormalizedDocument) -> str:
    payload = {
        "schema_version": document.schema_version,
        "paper_id": document.paper_id,
        "source_type": document.source_type,
        "segments": [
            {
                "text": segment.text,
                "section_title": segment.section_title,
                "page_number": segment.page_number,
            }
            for segment in document.segments
        ],
    }
    return _sha256_json(payload)


def embedded_chunks_fingerprint(chunks: list[EmbeddedChunk]) -> str:
    return _sha256_json(
        [chunk.model_dump(mode="json") for chunk in chunks]
    )


def _sha256_json(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_cached_chunks(
    chunks: list[EmbeddedChunk],
    *,
    paper_id: str,
    embedding_config: EmbeddingConfig,
) -> bool:
    for expected_index, chunk in enumerate(chunks):
        if (
            chunk.paper_id != paper_id
            or chunk.chunk_index != expected_index
            or chunk.model_name != embedding_config.model_name
            or len(chunk.embedding) != embedding_config.expected_dimension
            or not all(math.isfinite(value) for value in chunk.embedding)
        ):
            return False
        norm = math.sqrt(sum(value * value for value in chunk.embedding))
        if not math.isfinite(norm) or not math.isclose(
            norm,
            1.0,
            rel_tol=UNIT_VECTOR_TOLERANCE,
            abs_tol=UNIT_VECTOR_TOLERANCE,
        ):
            return False
    return True
