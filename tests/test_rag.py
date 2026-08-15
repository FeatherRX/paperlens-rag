from collections.abc import Iterable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.answer import AnswerService
from app.chunking import ChunkingService
from app.embedding import EmbeddingConfig, EmbeddingService
from app.ingestion import NormalizedDocument, TextSegment
from app.main import app, get_rag_answer_service
from app.models import RagAnswerRequest
from app.qwen import QwenConfigurationError, QwenLLMClient, QwenTimeoutError
from app.rag import RagAnswerService
from app.retrieval import RetrievalService


class FakeDocumentStore:
    def __init__(self, documents: dict[str, NormalizedDocument]) -> None:
        self.documents = documents
        self.loaded_ids: list[str] = []

    def load(self, paper_id: str) -> NormalizedDocument | None:
        self.loaded_ids.append(paper_id)
        return self.documents.get(paper_id)


class FakeEncoder:
    def __init__(self, vectors_by_text: dict[str, list[float]]) -> None:
        self.vectors_by_text = vectors_by_text
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
        yield [1.0, 0.0, 0.0]


class MockQwenClient:
    def __init__(
        self,
        response: str = "The answer is supported by evidence [1].",
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _document(
    paper_id: str,
    title: str,
    text: str,
    *,
    page_number: int | None = None,
    section_title: str | None = None,
) -> NormalizedDocument:
    return NormalizedDocument(
        paper_id=paper_id,
        title=title,
        license="cc-by",
        source_type="pdf",
        segments=[
            TextSegment(
                text=text,
                page_number=page_number,
                section_title=section_title,
            )
        ],
        character_count=len(text),
        ingested_at=datetime.now(UTC).isoformat(),
    )


def _rag_service(
    documents: dict[str, NormalizedDocument],
    *,
    llm_response: str = "The answer is supported by evidence [1].",
    llm_error: Exception | None = None,
) -> tuple[RagAnswerService, FakeDocumentStore, FakeEncoder, MockQwenClient]:
    store = FakeDocumentStore(documents)
    encoder = FakeEncoder(
        {
            "Active retrieval uses uncertainty signals.": [1.0, 0.0, 0.0],
            "Retrieval can support factual generation.": [0.8, 0.6, 0.0],
            "Unrelated experimental details.": [0.0, 1.0, 0.0],
            "This cached paper was not selected.": [0.0, 0.0, 1.0],
        }
    )
    embedding_service = EmbeddingService(
        encoder=encoder,
        config=EmbeddingConfig(
            model_name="mock-model",
            expected_dimension=3,
            batch_size=8,
        ),
    )
    llm_client = MockQwenClient(llm_response, error=llm_error)
    service = RagAnswerService(
        AnswerService(llm_client),
        store=store,
        chunking_service=ChunkingService(),
        embedding_service=embedding_service,
        retrieval_service=RetrievalService(embedding_service),
    )
    return service, store, encoder, llm_client


def _documents() -> dict[str, NormalizedDocument]:
    return {
        "W100": _document(
            "W100",
            "Active Retrieval",
            "Active retrieval uses uncertainty signals.",
            page_number=3,
            section_title="Method",
        ),
        "W200": _document(
            "W200",
            "Retrieval Background",
            "Retrieval can support factual generation.",
            page_number=7,
            section_title="Background",
        ),
        "W300": _document(
            "W300",
            "Other Results",
            "Unrelated experimental details.",
            page_number=11,
            section_title="Results",
        ),
        "W999": _document(
            "W999",
            "Unselected Cache",
            "This cached paper was not selected.",
        ),
    }


def _override_rag_service(service: RagAnswerService) -> None:
    app.dependency_overrides[get_rag_answer_service] = lambda: service


def test_rag_answer_runs_selected_corpus_and_returns_stable_citations() -> None:
    service, store, encoder, llm_client = _rag_service(_documents())
    _override_rag_service(service)

    with TestClient(app) as client:
        response = client.post(
            "/rag/answer",
            json={
                "query": "  When should retrieval happen?  ",
                "paper_ids": [
                    "W100",
                    "https://openalex.org/W200",
                    "W300",
                ],
                "top_k": 2,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "The answer is supported by evidence [1]."
    assert [citation["citation_number"] for citation in payload["citations"]] == [
        1,
        2,
    ]
    assert payload["citations"][0] == {
        "citation_number": 1,
        "paper_id": "W100",
        "paper_title": "Active Retrieval",
        "chunk_index": 0,
        "page_numbers": [3],
        "section_title": "Method",
        "evidence_excerpt": "Active retrieval uses uncertainty signals.",
        "retrieval_score": pytest.approx(1.0),
    }
    assert payload["citations"][1]["paper_id"] == "W200"
    assert payload["citations"][1]["retrieval_score"] == pytest.approx(0.8)
    assert store.loaded_ids == ["W100", "W200", "W300"]
    assert encoder.document_calls == [
        [
            "Active retrieval uses uncertainty signals.",
            "Retrieval can support factual generation.",
            "Unrelated experimental details.",
        ]
    ]
    assert encoder.query_calls == ["When should retrieval happen?"]
    assert len(llm_client.prompts) == 1
    prompt = llm_client.prompts[0]
    assert prompt.index("[1]\npaper_id:") < prompt.index("[2]\npaper_id:")
    assert "paper_id: W100" in prompt
    assert "paper_id: W200" in prompt
    assert "paper_id: W999" not in prompt


def test_rag_request_defaults_top_k_to_five() -> None:
    request = RagAnswerRequest(
        query="Question",
        paper_ids=["W100", "W200", "W300"],
    )

    assert request.top_k == 5


@pytest.mark.parametrize(
    "request_payload",
    [
        {"query": "Question", "paper_ids": ["W100", "W200"]},
        {
            "query": "Question",
            "paper_ids": ["W100", "https://openalex.org/W100", "W300"],
        },
        {"query": "   ", "paper_ids": ["W100", "W200", "W300"]},
        {
            "query": "Question",
            "paper_ids": ["W100", "W200", "W300"],
            "top_k": 0,
        },
    ],
)
def test_rag_request_validation_returns_422(request_payload: object) -> None:
    service, _, _, _ = _rag_service(_documents())
    _override_rag_service(service)

    with TestClient(app) as client:
        response = client.post("/rag/answer", json=request_payload)

    assert response.status_code == 422


def test_missing_ingested_paper_returns_stable_404() -> None:
    documents = _documents()
    del documents["W200"]
    service, store, _, llm_client = _rag_service(documents)
    _override_rag_service(service)

    with TestClient(app) as client:
        response = client.post(
            "/rag/answer",
            json={
                "query": "Question",
                "paper_ids": ["W100", "W200", "W300"],
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "ingested_papers_not_found",
            "paper_ids": ["W200"],
        }
    }
    assert store.loaded_ids == ["W100", "W200", "W300"]
    assert llm_client.prompts == []


def test_empty_selected_corpus_returns_stable_422() -> None:
    empty_documents = {
        paper_id: NormalizedDocument(
            paper_id=paper_id,
            title=f"Paper {paper_id}",
            source_type="abstract",
            segments=[],
            character_count=0,
            ingested_at=datetime.now(UTC).isoformat(),
        )
        for paper_id in ("W100", "W200", "W300")
    }
    service, _, _, llm_client = _rag_service(empty_documents)
    _override_rag_service(service)

    with TestClient(app) as client:
        response = client.post(
            "/rag/answer",
            json={
                "query": "Question",
                "paper_ids": ["W100", "W200", "W300"],
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "rag_corpus_empty"
    assert llm_client.prompts == []


def test_llm_error_returns_stable_502() -> None:
    service, _, _, llm_client = _rag_service(
        _documents(),
        llm_error=RuntimeError("provider unavailable"),
    )
    _override_rag_service(service)

    with TestClient(app) as client:
        response = client.post(
            "/rag/answer",
            json={
                "query": "Question",
                "paper_ids": ["W100", "W200", "W300"],
            },
        )

    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "code": "llm_request_failed",
            "message": "The answer model request failed",
        }
    }
    assert len(llm_client.prompts) == 1


def test_llm_timeout_returns_stable_504() -> None:
    service, _, _, _ = _rag_service(
        _documents(),
        llm_error=QwenTimeoutError("timeout"),
    )
    _override_rag_service(service)

    with TestClient(app) as client:
        response = client.post(
            "/rag/answer",
            json={
                "query": "Question",
                "paper_ids": ["W100", "W200", "W300"],
            },
        )

    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "llm_request_timeout"


def test_missing_qwen_configuration_returns_stable_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_from_env(*args: object, **kwargs: object) -> QwenLLMClient:
        raise QwenConfigurationError("configuration unavailable")

    monkeypatch.setattr(QwenLLMClient, "from_env", fail_from_env)

    with TestClient(app) as client:
        response = client.post(
            "/rag/answer",
            json={
                "query": "Question",
                "paper_ids": ["W100", "W200", "W300"],
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "llm_configuration_error",
            "message": "DashScope configuration is missing or invalid",
        }
    }
