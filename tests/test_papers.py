from collections.abc import AsyncIterator, Callable, Iterator

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.openalex import (
    OPENALEX_BASE_URL,
    OpenAlexClient,
    get_openalex_api_key,
    get_openalex_client,
)


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _mock_openalex(
    handler: Callable[[httpx2.Request], httpx2.Response],
    api_key: str | None = None,
) -> None:
    async def override() -> AsyncIterator[OpenAlexClient]:
        transport = httpx2.MockTransport(handler)
        async with httpx2.AsyncClient(
            base_url=OPENALEX_BASE_URL,
            transport=transport,
        ) as http_client:
            yield OpenAlexClient(http_client, api_key=api_key)

    app.dependency_overrides[get_openalex_client] = override


def test_search_papers_success() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.params["search"] == "retrieval augmented generation"
        assert request.url.params["per_page"] == "10"
        assert "abstract_inverted_index" in request.url.params["select"]
        return httpx2.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W123",
                        "title": "A PaperLens Study",
                        "authorships": [
                            {
                                "author": {"display_name": "Ada Lovelace"},
                                "institutions": [
                                    {"display_name": "Analytical Institute"}
                                ],
                            },
                            {
                                "author": {"display_name": "Grace Hopper"},
                                "institutions": [
                                    {"display_name": "Analytical Institute"},
                                    {"display_name": "Computing Lab"},
                                ],
                            },
                        ],
                        "publication_year": 2026,
                        "abstract_inverted_index": {
                            "evidence.": [2],
                            "PaperLens": [0, 3],
                            "finds": [1],
                        },
                        "doi": "https://doi.org/10.1000/paperlens",
                        "primary_location": {
                            "landing_page_url": "https://example.org/paper"
                        },
                        "best_oa_location": None,
                        "open_access": {
                            "is_oa": True,
                            "oa_status": "gold",
                            "oa_url": "https://example.org/paper",
                            "any_repository_has_fulltext": True,
                        },
                        "cited_by_count": 42,
                    },
                    {
                        "id": "https://openalex.org/W456",
                        "display_name": "A Work With Missing Metadata",
                    },
                ]
            },
        )

    _mock_openalex(handler)

    with TestClient(app) as client:
        response = client.get(
            "/papers/search",
            params={"query": "  retrieval augmented generation  "},
        )

    assert response.status_code == 200
    assert response.json() == {
        "query": "retrieval augmented generation",
        "count": 2,
        "papers": [
            {
                "id": "https://openalex.org/W123",
                "title": "A PaperLens Study",
                "authors": ["Ada Lovelace", "Grace Hopper"],
                "institutions": ["Analytical Institute", "Computing Lab"],
                "publication_year": 2026,
                "abstract": "PaperLens finds evidence. PaperLens",
                "doi": "https://doi.org/10.1000/paperlens",
                "landing_page_url": "https://example.org/paper",
                "open_access": {
                    "is_oa": True,
                    "status": "gold",
                    "oa_url": "https://example.org/paper",
                    "any_repository_has_fulltext": True,
                },
                "cited_by_count": 42,
            },
            {
                "id": "https://openalex.org/W456",
                "title": "A Work With Missing Metadata",
                "authors": [],
                "institutions": [],
                "publication_year": None,
                "abstract": None,
                "doi": None,
                "landing_page_url": None,
                "open_access": None,
                "cited_by_count": None,
            },
        ],
    }


def test_search_papers_includes_configured_api_key() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.params["api_key"] == "example-test-key"
        return httpx2.Response(200, json={"results": []})

    _mock_openalex(handler, api_key="example-test-key")

    with TestClient(app) as client:
        response = client.get("/papers/search", params={"query": "RAG"})

    assert response.status_code == 200


def test_search_papers_uses_anonymous_request_without_api_key() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert "api_key" not in request.url.params
        return httpx2.Response(200, json={"results": []})

    _mock_openalex(handler)

    with TestClient(app) as client:
        response = client.get("/papers/search", params={"query": "RAG"})

    assert response.status_code == 200


def test_openalex_api_key_loads_from_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "OPENALEX_API_KEY=example-test-key\n",
        encoding="utf-8",
    )

    assert get_openalex_api_key(dotenv_path) == "example-test-key"


def test_search_papers_rejects_blank_query_without_network() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(f"Unexpected network request: {request.url}")

    _mock_openalex(handler)

    with TestClient(app) as client:
        response = client.get("/papers/search", params={"query": "   "})

    assert response.status_code == 422
    assert response.json() == {"detail": "query must not be blank"}


def test_search_papers_returns_empty_result() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"results": []})

    _mock_openalex(handler)

    with TestClient(app) as client:
        response = client.get(
            "/papers/search",
            params={"query": "topic with no papers", "limit": 3},
        )

    assert response.status_code == 200
    assert response.json() == {
        "query": "topic with no papers",
        "count": 0,
        "papers": [],
    }


def test_search_papers_maps_openalex_timeout_to_504() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("OpenAlex timed out", request=request)

    _mock_openalex(handler)

    with TestClient(app) as client:
        response = client.get("/papers/search", params={"query": "RAG"})

    assert response.status_code == 504
    assert response.json() == {"detail": "OpenAlex request timed out"}


def test_search_papers_maps_openalex_error_to_502() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, json={"error": "service unavailable"})

    _mock_openalex(handler)

    with TestClient(app) as client:
        response = client.get("/papers/search", params={"query": "RAG"})

    assert response.status_code == 502
    assert response.json() == {"detail": "OpenAlex request failed"}
