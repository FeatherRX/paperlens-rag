from collections.abc import AsyncIterator, Callable, Iterator

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.openalex import OPENALEX_BASE_URL, OpenAlexClient, get_openalex_client


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


def _work(
    paper_id: str,
    *,
    abstract: bool = True,
    best_oa_location: dict[str, object] | None = None,
    has_content: dict[str, bool] | None = None,
    content_urls: dict[str, str | None] | None = None,
) -> dict[str, object]:
    return {
        "id": f"https://openalex.org/{paper_id}",
        "title": f"Paper {paper_id}",
        "authorships": [
            {
                "author": {"display_name": "Ada Lovelace"},
                "institutions": [
                    {"display_name": "Analytical Institute"}
                ],
            }
        ],
        "publication_year": 2026,
        "abstract_inverted_index": (
            {"Prepared": [0], "abstract.": [1]} if abstract else None
        ),
        "doi": f"https://doi.org/10.1000/{paper_id.lower()}",
        "primary_location": {
            "landing_page_url": f"https://example.org/{paper_id}"
        },
        "best_oa_location": best_oa_location,
        "open_access": {
            "is_oa": bool(best_oa_location),
            "oa_status": "green" if best_oa_location else "closed",
            "oa_url": None,
            "any_repository_has_fulltext": False,
        },
        "cited_by_count": 7,
        "has_content": has_content or {
            "pdf": False,
            "grobid_xml": False,
        },
        "content_urls": content_urls,
    }


def test_prepare_three_short_ids_and_source_statuses() -> None:
    works = {
        "W101": _work(
            "W101",
            best_oa_location={
                "is_oa": True,
                "pdf_url": "https://example.org/W101.pdf",
                "license": "cc-by",
            },
            has_content={"pdf": True, "grobid_xml": True},
            content_urls={
                "pdf": "https://content.openalex.org/W101.pdf",
                "grobid_xml": "https://content.openalex.org/W101.xml",
            },
        ),
        "W102": _work("W102"),
        "W103": _work(
            "W103",
            abstract=False,
            has_content={"pdf": True, "grobid_xml": False},
            content_urls={
                "pdf": "https://content.openalex.org/W103.pdf",
                "grobid_xml": None,
            },
        ),
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        paper_id = request.url.path.rsplit("/", 1)[-1]
        assert request.url.params["select"]
        return httpx2.Response(200, json=works[paper_id])

    _mock_openalex(handler)

    with TestClient(app) as client:
        response = client.post(
            "/papers/prepare",
            json={"paper_ids": ["W101", "W102", "W103"]},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    assert [paper["source_status"] for paper in payload["papers"]] == [
        "fulltext_candidate",
        "abstract_only",
        "unavailable",
    ]

    candidate = payload["papers"][0]
    assert candidate["fulltext_url"] == "https://example.org/W101.pdf"
    assert candidate["fulltext_license"] == "cc-by"
    assert candidate["openalex_content"] == {
        "pdf_available": True,
        "grobid_xml_available": True,
        "content_url": "https://content.openalex.org/W101.xml",
    }
    assert candidate["authors"] == ["Ada Lovelace"]
    assert candidate["institutions"] == ["Analytical Institute"]
    assert candidate["abstract"] == "Prepared abstract."

    cached_only = payload["papers"][2]
    assert cached_only["openalex_content"]["pdf_available"] is True
    assert cached_only["source_status"] == "unavailable"
    assert cached_only["fulltext_url"] is None


def test_prepare_accepts_mixed_full_urls_and_short_ids() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested_paths.append(request.url.path)
        paper_id = request.url.path.rsplit("/", 1)[-1]
        return httpx2.Response(200, json=_work(paper_id))

    _mock_openalex(handler)

    with TestClient(app) as client:
        response = client.post(
            "/papers/prepare",
            json={
                "paper_ids": [
                    "https://openalex.org/W201",
                    "W202",
                    "https://openalex.org/W203",
                ]
            },
        )

    assert response.status_code == 200
    assert requested_paths == [
        "/works/W201",
        "/works/W202",
        "/works/W203",
    ]


@pytest.mark.parametrize(
    "paper_ids",
    [
        ["W101", "W102"],
        ["W101", "W102", "W103", "W104", "W105", "W106"],
        ["W101", "not-an-openalex-id", "W103"],
        ["W101", "https://openalex.org/W101", "W103"],
    ],
    ids=["fewer-than-three", "more-than-five", "invalid-id", "duplicate-id"],
)
def test_prepare_rejects_invalid_selection(paper_ids: list[str]) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(f"Unexpected OpenAlex request: {request.url.path}")

    _mock_openalex(handler)

    with TestClient(app) as client:
        response = client.post(
            "/papers/prepare",
            json={"paper_ids": paper_ids},
        )

    assert response.status_code == 422


def test_has_content_pdf_does_not_imply_fulltext_candidate() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        paper_id = request.url.path.rsplit("/", 1)[-1]
        return httpx2.Response(
            200,
            json=_work(
                paper_id,
                has_content={"pdf": True, "grobid_xml": True},
                content_urls={
                    "pdf": "https://content.openalex.org/cached.pdf",
                    "grobid_xml": "https://content.openalex.org/cached.xml",
                },
            ),
        )

    _mock_openalex(handler)

    with TestClient(app) as client:
        response = client.post(
            "/papers/prepare",
            json={"paper_ids": ["W301", "W302", "W303"]},
        )

    assert response.status_code == 200
    assert all(
        paper["source_status"] == "abstract_only"
        for paper in response.json()["papers"]
    )
    assert all(
        paper["fulltext_url"] is None for paper in response.json()["papers"]
    )


def test_prepare_maps_missing_paper_to_404() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        paper_id = request.url.path.rsplit("/", 1)[-1]
        if paper_id == "W402":
            return httpx2.Response(404, json={"error": "not found"})
        return httpx2.Response(200, json=_work(paper_id))

    _mock_openalex(handler)

    with TestClient(app) as client:
        response = client.post(
            "/papers/prepare",
            json={"paper_ids": ["W401", "W402", "W403"]},
        )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "paper_not_found",
            "paper_id": "W402",
        }
    }


def test_prepare_maps_openalex_timeout_to_504() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("OpenAlex timed out", request=request)

    _mock_openalex(handler)

    with TestClient(app) as client:
        response = client.post(
            "/papers/prepare",
            json={"paper_ids": ["W501", "W502", "W503"]},
        )

    assert response.status_code == 504
    assert response.json() == {"detail": "OpenAlex request timed out"}


def test_prepare_maps_openalex_http_error_to_502() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, json={"error": "service unavailable"})

    _mock_openalex(handler)

    with TestClient(app) as client:
        response = client.post(
            "/papers/prepare",
            json={"paper_ids": ["W601", "W602", "W603"]},
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "OpenAlex request failed"}


def test_prepare_includes_configured_api_key() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.params["api_key"] == "example-test-key"
        paper_id = request.url.path.rsplit("/", 1)[-1]
        return httpx2.Response(200, json=_work(paper_id))

    _mock_openalex(handler, api_key="example-test-key")

    with TestClient(app) as client:
        response = client.post(
            "/papers/prepare",
            json={"paper_ids": ["W701", "W702", "W703"]},
        )

    assert response.status_code == 200


def test_prepare_uses_anonymous_requests_without_api_key() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert "api_key" not in request.url.params
        paper_id = request.url.path.rsplit("/", 1)[-1]
        return httpx2.Response(200, json=_work(paper_id))

    _mock_openalex(handler)

    with TestClient(app) as client:
        response = client.post(
            "/papers/prepare",
            json={"paper_ids": ["W801", "W802", "W803"]},
        )

    assert response.status_code == 200
