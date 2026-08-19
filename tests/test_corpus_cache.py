import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.answer import AnswerService
from app.chunking import Chunk, ChunkingConfig, ChunkingService
from app.corpus_cache import (
    CORPUS_CACHE_SCHEMA_VERSION,
    CorpusEmbeddingCacheStore,
    normalized_document_fingerprint,
)
from app.embedding import EmbeddedChunk, EmbeddingConfig, EmbeddingService
from app.ingestion import JsonDocumentStore, NormalizedDocument, TextSegment
from app.rag import RagAnswerService
from app.retrieval import RetrievalResult, RetrievalService


class FakeDocumentStore:
    def __init__(self, documents: dict[str, NormalizedDocument]) -> None:
        self.documents = documents

    def load(self, paper_id: str) -> NormalizedDocument | None:
        return self.documents.get(paper_id)


class CountingChunkingService(ChunkingService):
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        super().__init__(config)
        self.calls: list[str] = []

    def chunk_document(self, document: NormalizedDocument) -> list[Chunk]:
        self.calls.append(document.paper_id)
        return super().chunk_document(document)


class FakeEncoder:
    def __init__(
        self,
        vectors_by_text: dict[str, list[float]],
        dimension: int,
    ) -> None:
        self.vectors_by_text = vectors_by_text
        self.dimension = dimension
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    def embed(
        self,
        documents: list[str],
        *,
        batch_size: int,
    ) -> Iterable[object]:
        self.document_calls.append(documents)
        return (self.vectors_by_text[text] for text in documents)

    def query_embed(self, query: str) -> Iterable[object]:
        self.query_calls.append(query)
        yield [1.0, *([0.0] * (self.dimension - 1))]


class StaticLLMClient:
    def generate(self, prompt: str) -> str:
        return "Cached evidence supports the answer [1]."


class CapturingRetrievalService(RetrievalService):
    def __init__(self, embedding_service: EmbeddingService) -> None:
        super().__init__(embedding_service)
        self.corpora: list[list[EmbeddedChunk]] = []

    def retrieve(
        self,
        query: str,
        corpus: Sequence[EmbeddedChunk],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        self.corpora.append(list(corpus))
        return super().retrieve(query, corpus, top_k=top_k)


def _document(
    paper_id: str,
    text: str,
    *,
    section_title: str | None = None,
    page_number: int | None = None,
    segments: list[TextSegment] | None = None,
    schema_version: int = 2,
) -> NormalizedDocument:
    document_segments = segments or [
        TextSegment(
            text=text,
            section_title=section_title,
            page_number=page_number,
        )
    ]
    return NormalizedDocument(
        schema_version=schema_version,
        paper_id=paper_id,
        title=f"Paper {paper_id}",
        license="cc-by",
        source_type="pdf",
        segments=document_segments,
        character_count=sum(len(segment.text) for segment in document_segments),
        ingested_at=datetime.now(UTC).isoformat(),
    )


def _vectors(
    documents: dict[str, NormalizedDocument],
    dimension: int,
) -> dict[str, list[float]]:
    vectors: dict[str, list[float]] = {}
    for index, document in enumerate(documents.values()):
        vector = [0.0] * dimension
        vector[index % dimension] = 1.0
        for segment in document.segments:
            vectors[segment.text] = vector
    return vectors


def _service(
    documents: dict[str, NormalizedDocument],
    cache_root: Path,
    *,
    chunking_config: ChunkingConfig | None = None,
    embedding_config: EmbeddingConfig | None = None,
    chunking_algorithm_version: str = "ordered-character-window-v1",
    embedding_pipeline_version: str = "fastembed-l2-v1",
) -> tuple[
    RagAnswerService,
    CountingChunkingService,
    FakeEncoder,
    CapturingRetrievalService,
]:
    config = embedding_config or EmbeddingConfig(
        model_name="mock-model",
        expected_dimension=3,
        batch_size=8,
    )
    encoder = FakeEncoder(
        _vectors(documents, config.expected_dimension),
        config.expected_dimension,
    )
    embedding_service = EmbeddingService(encoder=encoder, config=config)
    chunking_service = CountingChunkingService(chunking_config)
    retrieval_service = CapturingRetrievalService(embedding_service)
    service = RagAnswerService(
        AnswerService(StaticLLMClient()),
        store=FakeDocumentStore(documents),
        chunking_service=chunking_service,
        embedding_service=embedding_service,
        retrieval_service=retrieval_service,
        corpus_cache=CorpusEmbeddingCacheStore(
            cache_root,
            chunking_algorithm_version=chunking_algorithm_version,
            embedding_pipeline_version=embedding_pipeline_version,
        ),
    )
    return service, chunking_service, encoder, retrieval_service


def _answer(
    service: RagAnswerService,
    paper_ids: list[str],
) -> None:
    service.generate_answer("When should retrieval happen?", paper_ids, top_k=3)


def test_first_request_builds_cache_and_second_request_skips_corpus_work(
    tmp_path: Path,
) -> None:
    documents = {"W100": _document("W100", "Evidence text.")}
    cache_root = tmp_path / "ingested" / ".corpus-embeddings"
    first, first_chunking, first_encoder, _ = _service(
        documents, cache_root
    )

    _answer(first, ["W100"])

    cache_path = cache_root / "W100.json"
    assert cache_path.is_file()
    assert first_chunking.calls == ["W100"]
    assert first_encoder.document_calls == [["Evidence text."]]
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["cache_schema_version"] == CORPUS_CACHE_SCHEMA_VERSION
    assert payload["paper_id"] == "W100"
    assert payload["document_schema_version"] == 2
    assert payload["chunking"] == {
        "algorithm_version": "ordered-character-window-v1",
        "max_characters": 1200,
        "overlap_characters": 150,
    }
    assert payload["embedding"] == {
        "pipeline_version": "fastembed-l2-v1",
        "model_name": "mock-model",
        "dimension": 3,
        "normalization": "l2",
    }
    assert len(payload["normalized_document_fingerprint"]) == 64
    assert len(payload["chunks_fingerprint"]) == 64
    assert list(cache_root.glob("*.tmp")) == []

    second, second_chunking, second_encoder, _ = _service(
        documents, cache_root
    )
    _answer(second, ["W100"])

    assert second_chunking.calls == []
    assert second_encoder.document_calls == []
    assert second_encoder.query_calls == ["When should retrieval happen?"]


def test_json_document_store_uses_persisted_cache_subdirectory(
    tmp_path: Path,
) -> None:
    document = _document("W100", "Evidence text.")
    document_store = JsonDocumentStore(tmp_path / "ingested")
    document_store.save(document)
    config = EmbeddingConfig(
        model_name="mock-model",
        expected_dimension=3,
        batch_size=8,
    )
    encoder = FakeEncoder({"Evidence text.": [1.0, 0.0, 0.0]}, 3)
    embedding_service = EmbeddingService(encoder=encoder, config=config)
    service = RagAnswerService(
        AnswerService(StaticLLMClient()),
        store=document_store,
        chunking_service=ChunkingService(),
        embedding_service=embedding_service,
        retrieval_service=RetrievalService(embedding_service),
    )

    _answer(service, ["W100"])

    assert (
        document_store.root / ".corpus-embeddings" / "W100.json"
    ).is_file()


def test_document_change_invalidates_cache(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    original = {"W100": _document("W100", "Original evidence.")}
    first, _, _, _ = _service(original, cache_root)
    _answer(first, ["W100"])

    changed = {"W100": _document("W100", "Updated evidence.")}
    second, chunking, encoder, _ = _service(changed, cache_root)
    _answer(second, ["W100"])

    assert chunking.calls == ["W100"]
    assert encoder.document_calls == [["Updated evidence."]]


def test_document_fingerprint_tracks_all_chunking_inputs() -> None:
    segments = [
        TextSegment(text="First", section_title="Intro", page_number=1),
        TextSegment(text="Second", section_title="Method", page_number=2),
    ]
    original = _document("W100", "unused", segments=segments)
    original_fingerprint = normalized_document_fingerprint(original)
    variants = [
        _document(
            "W100",
            "unused",
            segments=[
                TextSegment(
                    text="Changed", section_title="Intro", page_number=1
                ),
                segments[1],
            ],
        ),
        _document("W100", "unused", segments=list(reversed(segments))),
        _document(
            "W100",
            "unused",
            segments=[
                TextSegment(
                    text="First", section_title="Background", page_number=1
                ),
                segments[1],
            ],
        ),
        _document(
            "W100",
            "unused",
            segments=[
                TextSegment(
                    text="First", section_title="Intro", page_number=9
                ),
                segments[1],
            ],
        ),
        _document("W100", "unused", segments=segments, schema_version=3),
    ]

    assert all(
        normalized_document_fingerprint(variant) != original_fingerprint
        for variant in variants
    )


@pytest.mark.parametrize(
    ("chunking_config", "algorithm_version"),
    [
        (
            ChunkingConfig(max_characters=600, overlap_characters=75),
            "ordered-character-window-v1",
        ),
        (ChunkingConfig(), "ordered-character-window-v2"),
    ],
)
def test_chunking_signature_change_invalidates_cache(
    tmp_path: Path,
    chunking_config: ChunkingConfig,
    algorithm_version: str,
) -> None:
    documents = {"W100": _document("W100", "Evidence text.")}
    first, _, _, _ = _service(documents, tmp_path)
    _answer(first, ["W100"])

    second, chunking, encoder, _ = _service(
        documents,
        tmp_path,
        chunking_config=chunking_config,
        chunking_algorithm_version=algorithm_version,
    )
    _answer(second, ["W100"])

    assert chunking.calls == ["W100"]
    assert encoder.document_calls == [["Evidence text."]]


@pytest.mark.parametrize(
    ("embedding_config", "pipeline_version"),
    [
        (
            EmbeddingConfig(
                model_name="other-model",
                expected_dimension=3,
                batch_size=8,
            ),
            "fastembed-l2-v1",
        ),
        (
            EmbeddingConfig(
                model_name="mock-model",
                expected_dimension=2,
                batch_size=8,
            ),
            "fastembed-l2-v1",
        ),
        (
            EmbeddingConfig(
                model_name="mock-model",
                expected_dimension=3,
                batch_size=8,
            ),
            "fastembed-l2-v2",
        ),
    ],
)
def test_embedding_signature_change_invalidates_cache(
    tmp_path: Path,
    embedding_config: EmbeddingConfig,
    pipeline_version: str,
) -> None:
    documents = {"W100": _document("W100", "Evidence text.")}
    first, _, _, _ = _service(documents, tmp_path)
    _answer(first, ["W100"])

    second, chunking, encoder, _ = _service(
        documents,
        tmp_path,
        embedding_config=embedding_config,
        embedding_pipeline_version=pipeline_version,
    )
    _answer(second, ["W100"])

    assert chunking.calls == ["W100"]
    assert encoder.document_calls == [["Evidence text."]]


def test_corrupt_json_is_treated_as_miss_and_rebuilt(tmp_path: Path) -> None:
    documents = {"W100": _document("W100", "Evidence text.")}
    first, _, _, _ = _service(documents, tmp_path)
    _answer(first, ["W100"])
    cache_path = tmp_path / "W100.json"
    cache_path.write_text("{not-json", encoding="utf-8")

    second, chunking, encoder, _ = _service(documents, tmp_path)
    _answer(second, ["W100"])

    assert chunking.calls == ["W100"]
    assert encoder.document_calls == [["Evidence text."]]
    assert json.loads(cache_path.read_text(encoding="utf-8"))["paper_id"] == "W100"


@pytest.mark.parametrize(
    "invalid_embedding",
    [
        [1.0, 0.0],
        [float("nan"), 0.0, 0.0],
        [float("inf"), 0.0, 0.0],
    ],
)
def test_invalid_cached_embedding_is_treated_as_miss(
    tmp_path: Path,
    invalid_embedding: list[float],
) -> None:
    documents = {"W100": _document("W100", "Evidence text.")}
    first, _, _, _ = _service(documents, tmp_path)
    _answer(first, ["W100"])
    cache_path = tmp_path / "W100.json"
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["chunks"][0]["embedding"] = invalid_embedding
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    second, chunking, encoder, _ = _service(documents, tmp_path)
    _answer(second, ["W100"])

    assert chunking.calls == ["W100"]
    assert encoder.document_calls == [["Evidence text."]]


def test_multiple_papers_preserve_request_order_and_chunk_metadata(
    tmp_path: Path,
) -> None:
    documents = {
        "W100": _document(
            "W100", "First paper.", section_title="Intro", page_number=1
        ),
        "W200": _document(
            "W200", "Second paper.", section_title="Methods", page_number=4
        ),
        "W300": _document(
            "W300", "Third paper.", section_title="Results", page_number=9
        ),
    }
    warm_service, _, _, _ = _service(
        {"W100": documents["W100"]},
        tmp_path,
    )
    _answer(warm_service, ["W100"])

    service, chunking, encoder, retrieval = _service(documents, tmp_path)

    _answer(service, ["W300", "W100", "W200"])

    assert chunking.calls == ["W300", "W200"]
    assert encoder.document_calls == [["Third paper.", "Second paper."]]
    corpus = retrieval.corpora[0]
    assert [chunk.paper_id for chunk in corpus] == ["W300", "W100", "W200"]
    assert [chunk.section_title for chunk in corpus] == [
        "Results",
        "Intro",
        "Methods",
    ]
    assert [chunk.page_numbers for chunk in corpus] == [[9], [1], [4]]
    assert [chunk.chunk_index for chunk in corpus] == [0, 0, 0]
    assert sorted(path.name for path in tmp_path.glob("*.json")) == [
        "W100.json",
        "W200.json",
        "W300.json",
    ]
