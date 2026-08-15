import gzip
import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.ingestion import (
    FULLTEXT_FAILURE_RETRY_COOLDOWN,
    IngestionParseError,
    JsonDocumentStore,
    PaperIngestionService,
    _content_candidates,
    is_allowed_fulltext_license,
    parse_tei_xml,
)
from app.main import app, get_ingestion_service
from app.openalex import (
    OPENALEX_BASE_URL,
    OpenAlexClient,
    canonical_content_url,
)


TEI_CONTENT = b"\xef\xbb\xbf" + b"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text>
    <body>
      <div><head>Introduction</head><p>First paragraph.</p></div>
      <div><head>Methods</head><p>Second paragraph.</p></div>
      <div><head>References</head><p>Not body evidence.</p></div>
      <div type="references"><p>Also not body evidence.</p></div>
      <div><head></head><p>   </p></div>
    </body>
    <back><div><listBibl><biblStruct><p>Not body text.</p></biblStruct></listBibl></div></back>
  </text>
</TEI>
"""

HTML_GROBID_CONTENT = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <p>First <span>nested</span> inline <em>text.</em></p>
    <section>
      <p>Second paragraph.</p>
      <p>   </p>
    </section>
    <section class="bibliography">
      <p>Excluded bibliography paragraph.</p>
    </section>
    <listBibl>
      <biblStruct><p>Excluded reference paragraph.</p></biblStruct>
    </listBibl>
  </body>
</html>
"""


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _abstract_index(enabled: bool) -> dict[str, list[int]] | None:
    return {"Original": [0], "abstract": [1], "text.": [2]} if enabled else None


def _work(
    paper_id: str,
    *,
    license_value: str | None = "cc-by",
    is_oa: bool = True,
    abstract: bool = True,
    grobid_xml: bool = False,
    pdf: bool = False,
    grobid_url: str | None = None,
    pdf_url: str | None = None,
) -> dict[str, object]:
    return {
        "id": f"https://openalex.org/{paper_id}",
        "title": f"Paper {paper_id}",
        "authorships": [],
        "publication_year": 2026,
        "abstract_inverted_index": _abstract_index(abstract),
        "doi": None,
        "primary_location": None,
        "best_oa_location": {
            "is_oa": is_oa,
            "pdf_url": None,
            "license": license_value,
        },
        "open_access": {"is_oa": is_oa},
        "cited_by_count": 0,
        "has_content": {"grobid_xml": grobid_xml, "pdf": pdf},
        "content_urls": {
            "grobid_xml": grobid_url,
            "pdf": pdf_url,
        },
        "updated_date": "2026-08-14T00:00:00Z",
    }


def _mock_ingestion(
    handler: Callable[[httpx2.Request], httpx2.Response],
    cache_directory: Path,
    *,
    api_key: str | None = "configured-test-key",
) -> None:
    async def override() -> AsyncIterator[PaperIngestionService]:
        transport = httpx2.MockTransport(handler)
        async with httpx2.AsyncClient(
            base_url=OPENALEX_BASE_URL,
            transport=transport,
        ) as http_client:
            yield PaperIngestionService(
                OpenAlexClient(http_client, api_key=api_key),
                JsonDocumentStore(cache_directory),
            )

    app.dependency_overrides[get_ingestion_service] = override


def _make_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        b"5 0 obj\n<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream\nendobj\n",
    ]
    content = b"%PDF-1.4\n"
    offsets = [0]
    for item in objects:
        offsets.append(len(content))
        content += item
    xref_offset = len(content)
    content += b"xref\n0 6\n0000000000 65535 f \n"
    content += b"".join(
        f"{offset:010d} 00000 n \n".encode("ascii")
        for offset in offsets[1:]
    )
    content += (
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    return content


def _post_ingest(client: TestClient) -> httpx2.Response:
    return client.post(
        "/papers/ingest",
        json={"paper_ids": ["W101", "W102", "W103"]},
    )


@pytest.mark.parametrize(
    "license_value",
    ["cc-by", "cc-by-sa", "cc0", "public-domain", " CC-BY "],
)
def test_allowed_fulltext_licenses(license_value: str) -> None:
    assert is_allowed_fulltext_license(license_value) is True


def test_ingest_prefers_tei_and_persists_sections(tmp_path: Path) -> None:
    requested_content_paths: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "api.openalex.org":
            paper_id = request.url.path.rsplit("/", 1)[-1]
            return httpx2.Response(
                200,
                json=_work(
                    paper_id,
                    grobid_xml=True,
                    pdf=True,
                    grobid_url=canonical_content_url(paper_id, "grobid_xml"),
                    pdf_url=canonical_content_url(paper_id, "pdf"),
                ),
            )
        requested_content_paths.append(request.url.path)
        assert request.url.host == "content.openalex.org"
        assert "api_key" in request.url.params
        return httpx2.Response(
            200,
            content=TEI_CONTENT,
            headers={"Content-Type": "application/xml; charset=utf-8"},
        )

    cache_directory = tmp_path / "ingested"
    _mock_ingestion(handler, cache_directory)

    with TestClient(app) as client:
        response = _post_ingest(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    assert [paper["status"] for paper in payload["papers"]] == [
        "ingested",
        "ingested",
        "ingested",
    ]
    assert all(paper["source_type"] == "grobid_xml" for paper in payload["papers"])
    assert all(paper["segment_count"] == 2 for paper in payload["papers"])
    assert all("segments" not in paper for paper in payload["papers"])
    assert requested_content_paths == [
        "/works/W101.grobid-xml",
        "/works/W102.grobid-xml",
        "/works/W103.grobid-xml",
    ]

    document = json.loads((cache_directory / "W101.json").read_text("utf-8"))
    assert document["paper_id"] == "W101"
    assert document["source_type"] == "grobid_xml"
    assert document["license"] == "cc-by"
    assert document["segments"] == [
        {
            "text": "First paragraph.",
            "page_number": None,
            "section_title": "Introduction",
        },
        {
            "text": "Second paragraph.",
            "page_number": None,
            "section_title": "Methods",
        },
    ]
    assert document["character_count"] == 33
    assert document["ingested_at"]
    assert document["source_version"] == "2026-08-14T00:00:00Z"
    assert list(cache_directory.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "application/gzip"},
        {
            "Content-Type": "application/xml",
            "Content-Encoding": "gzip",
        },
    ],
    ids=["gzip-media-type-and-magic", "content-encoding"],
)
def test_ingest_accepts_explicitly_gzipped_namespaced_tei(
    tmp_path: Path,
    headers: dict[str, str],
) -> None:
    compressed_content = gzip.compress(TEI_CONTENT)

    def handler(request: httpx2.Request) -> httpx2.Response:
        paper_id = request.url.path.rsplit("/", 1)[-1].split(".", 1)[0]
        if request.url.host == "api.openalex.org":
            return httpx2.Response(
                200,
                json=_work(paper_id, grobid_xml=True),
            )
        return httpx2.Response(
            200,
            stream=httpx2.ByteStream(compressed_content),
            headers=headers,
        )

    _mock_ingestion(handler, tmp_path / "ingested")
    with TestClient(app) as client:
        response = _post_ingest(client)

    assert response.status_code == 200
    assert all(
        paper["status"] == "ingested"
        and paper["source_type"] == "grobid_xml"
        and paper["segment_count"] == 2
        for paper in response.json()["papers"]
    )


def test_html_grobid_xml_extracts_nested_paragraphs_without_headings() -> None:
    segments = parse_tei_xml(HTML_GROBID_CONTENT)

    assert [segment.model_dump() for segment in segments] == [
        {
            "text": "First nested inline text.",
            "page_number": None,
            "section_title": None,
        },
        {
            "text": "Second paragraph.",
            "page_number": None,
            "section_title": None,
        },
    ]


def test_canonical_content_urls_and_metadata_variants() -> None:
    assert canonical_content_url("W123", "grobid_xml") == (
        "https://content.openalex.org/works/W123.grobid-xml"
    )
    assert canonical_content_url("W123", "pdf") == (
        "https://content.openalex.org/works/W123.pdf"
    )

    from_content_urls = _work("W123")
    from_content_urls["has_content"] = {}
    from_content_urls["content_urls"] = {
        "grobid_xml": canonical_content_url("W123", "grobid_xml"),
        "pdf": canonical_content_url("W123", "pdf"),
    }
    assert _content_candidates(from_content_urls, "W123") == [
        ("grobid_xml", canonical_content_url("W123", "grobid_xml")),
        ("pdf", canonical_content_url("W123", "pdf")),
    ]

    from_legacy_content_url = _work("W124")
    from_legacy_content_url["has_content"] = {}
    from_legacy_content_url["content_urls"] = {}
    from_legacy_content_url["content_url"] = canonical_content_url(
        "W124", "pdf"
    )
    assert _content_candidates(from_legacy_content_url, "W124") == [
        ("pdf", canonical_content_url("W124", "pdf"))
    ]


def test_ingest_uses_pdf_when_xml_is_unavailable(tmp_path: Path) -> None:
    pdf_content = _make_pdf("PDF source text")

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "api.openalex.org":
            paper_id = request.url.path.rsplit("/", 1)[-1]
            return httpx2.Response(
                200,
                json=_work(
                    paper_id,
                    abstract=False,
                    pdf=True,
                    pdf_url=canonical_content_url(paper_id, "pdf"),
                ),
            )
        assert request.url.path.endswith(".pdf")
        return httpx2.Response(
            200,
            content=pdf_content,
            headers={"Content-Type": "application/pdf"},
        )

    cache_directory = tmp_path / "ingested"
    _mock_ingestion(handler, cache_directory)

    with TestClient(app) as client:
        response = _post_ingest(client)

    assert response.status_code == 200
    assert all(
        paper["source_type"] == "pdf" and paper["status"] == "ingested"
        for paper in response.json()["papers"]
    )
    document = json.loads((cache_directory / "W101.json").read_text("utf-8"))
    assert document["segments"] == [
        {
            "text": "PDF source text",
            "page_number": 1,
            "section_title": None,
        }
    ]


def test_unknown_and_empty_licenses_never_download(tmp_path: Path) -> None:
    works = {
        "W101": _work(
            "W101",
            license_value="unknown-license",
            abstract=False,
            grobid_xml=True,
            grobid_url=canonical_content_url("W101", "grobid_xml"),
        ),
        "W102": _work(
            "W102",
            license_value=None,
            abstract=True,
            grobid_xml=True,
            grobid_url=canonical_content_url("W102", "grobid_xml"),
        ),
        "W103": _work(
            "W103",
            license_value="all-rights-reserved",
            abstract=False,
            pdf=True,
            pdf_url=canonical_content_url("W103", "pdf"),
        ),
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.host == "api.openalex.org"
        return httpx2.Response(
            200, json=works[request.url.path.rsplit("/", 1)[-1]]
        )

    _mock_ingestion(handler, tmp_path / "ingested")
    with TestClient(app) as client:
        response = _post_ingest(client)

    assert response.status_code == 200
    assert [paper["status"] for paper in response.json()["papers"]] == [
        "license_review_required",
        "abstract_fallback",
        "license_review_required",
    ]
    assert response.json()["papers"][1]["source_type"] == "abstract"


def test_missing_key_does_not_attempt_anonymous_content_download(
    tmp_path: Path,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.host == "api.openalex.org"
        assert "api_key" not in request.url.params
        paper_id = request.url.path.rsplit("/", 1)[-1]
        return httpx2.Response(
            200,
            json=_work(
                paper_id,
                grobid_xml=True,
                grobid_url=canonical_content_url(paper_id, "grobid_xml"),
            ),
        )

    _mock_ingestion(handler, tmp_path / "ingested", api_key=None)
    with TestClient(app) as client:
        response = _post_ingest(client)

    assert response.status_code == 200
    assert all(
        paper["status"] == "abstract_fallback"
        and paper["source_type"] == "abstract"
        for paper in response.json()["papers"]
    )
    assert all(
        "requires a configured OpenAlex API key" in paper["message"]
        for paper in response.json()["papers"]
    )


def test_abstract_fallback_and_unavailable_statuses(tmp_path: Path) -> None:
    works = {
        "W101": _work("W101", abstract=True),
        "W102": _work("W102", abstract=False),
        "W103": _work("W103", abstract=True),
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        paper_id = request.url.path.rsplit("/", 1)[-1]
        return httpx2.Response(200, json=works[paper_id])

    _mock_ingestion(handler, tmp_path / "ingested")
    with TestClient(app) as client:
        response = _post_ingest(client)

    assert response.status_code == 200
    assert [paper["status"] for paper in response.json()["papers"]] == [
        "abstract_fallback",
        "unavailable",
        "abstract_fallback",
    ]


def test_download_failures_are_isolated_and_valid_paper_continues(
    tmp_path: Path,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        paper_id = request.url.path.rsplit("/", 1)[-1].split(".", 1)[0]
        if request.url.host == "api.openalex.org":
            return httpx2.Response(
                200,
                json=_work(
                    paper_id,
                    abstract=False,
                    grobid_xml=True,
                    grobid_url=canonical_content_url(paper_id, "grobid_xml"),
                ),
            )
        if paper_id == "W101":
            raise httpx2.ReadTimeout("content timeout", request=request)
        if paper_id == "W102":
            return httpx2.Response(
                200,
                content=b"<TEI/>",
                headers={
                    "Content-Type": "application/xml",
                    "Content-Length": str(25 * 1024 * 1024 + 1),
                },
            )
        if paper_id == "W103":
            return httpx2.Response(
                200,
                content=TEI_CONTENT,
                headers={"Content-Type": "application/pdf"},
            )
        raise AssertionError("Unexpected paper ID")

    _mock_ingestion(handler, tmp_path / "ingested")
    with TestClient(app) as client:
        response = _post_ingest(client)

    assert response.status_code == 200
    assert response.json()["count"] == 3
    assert [paper["status"] for paper in response.json()["papers"]] == [
        "failed",
        "failed",
        "failed",
    ]
    assert "timed out" in response.json()["papers"][0]["message"]
    assert "maximum" in response.json()["papers"][1]["message"]
    assert "validation" in response.json()["papers"][2]["message"]


def test_one_failed_paper_does_not_prevent_later_success(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        paper_id = request.url.path.rsplit("/", 1)[-1].split(".", 1)[0]
        if request.url.host == "api.openalex.org":
            return httpx2.Response(
                200,
                json=_work(
                    paper_id,
                    abstract=False,
                    grobid_xml=True,
                    grobid_url=canonical_content_url(paper_id, "grobid_xml"),
                ),
            )
        if paper_id == "W101":
            return httpx2.Response(
                200,
                content=b"not XML",
                headers={"Content-Type": "application/xml"},
            )
        return httpx2.Response(
            200,
            content=TEI_CONTENT,
            headers={"Content-Type": "application/xml"},
        )

    _mock_ingestion(handler, tmp_path / "ingested")
    with TestClient(app) as client:
        response = _post_ingest(client)

    assert [paper["status"] for paper in response.json()["papers"]] == [
        "failed",
        "ingested",
        "ingested",
    ]


def test_cache_hit_does_not_repeat_content_download(tmp_path: Path) -> None:
    metadata_requests = 0
    content_requests = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal metadata_requests, content_requests
        paper_id = request.url.path.rsplit("/", 1)[-1].split(".", 1)[0]
        if request.url.host == "api.openalex.org":
            metadata_requests += 1
            return httpx2.Response(
                200,
                json=_work(
                    paper_id,
                    grobid_xml=True,
                    grobid_url=canonical_content_url(paper_id, "grobid_xml"),
                ),
            )
        content_requests += 1
        return httpx2.Response(
            200,
            content=TEI_CONTENT,
            headers={"Content-Type": "application/xml"},
        )

    _mock_ingestion(handler, tmp_path / "ingested")
    with TestClient(app) as client:
        first_response = _post_ingest(client)
        second_response = _post_ingest(client)

    assert all(
        paper["status"] == "ingested"
        for paper in first_response.json()["papers"]
    )
    assert all(
        paper["status"] == "cached"
        and paper["from_cache"] is True
        for paper in second_response.json()["papers"]
    )
    assert metadata_requests == 6
    assert content_requests == 3


def test_failed_content_fallback_is_cached_during_retry_cooldown(
    tmp_path: Path,
) -> None:
    content_requests = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal content_requests
        paper_id = request.url.path.rsplit("/", 1)[-1].split(".", 1)[0]
        if request.url.host == "api.openalex.org":
            if paper_id != "W101":
                return httpx2.Response(404)
            return httpx2.Response(
                200,
                json=_work(paper_id, grobid_xml=True),
            )
        content_requests += 1
        return httpx2.Response(
            200,
            content=b"not XML",
            headers={"Content-Type": "application/xml"},
        )

    cache_directory = tmp_path / "ingested"
    _mock_ingestion(handler, cache_directory)
    with TestClient(app) as client:
        first_response = _post_ingest(client)
        second_response = _post_ingest(client)

    assert first_response.json()["papers"][0]["status"] == "abstract_fallback"
    assert second_response.json()["papers"][0]["status"] == "cached"
    assert second_response.json()["papers"][0]["from_cache"] is True
    assert content_requests == 1
    assert FULLTEXT_FAILURE_RETRY_COOLDOWN.total_seconds() == 6 * 60 * 60

    document = json.loads((cache_directory / "W101.json").read_text("utf-8"))
    assert document["fulltext_attempt"] == {
        "content_requested": True,
        "failure_reason": "content_validation_failed",
        "attempted_at": document["fulltext_attempt"]["attempted_at"],
        "source_version": "2026-08-14T00:00:00Z",
    }
    assert document["fulltext_attempt"]["attempted_at"]


def test_source_version_change_allows_retry_after_failed_content(
    tmp_path: Path,
) -> None:
    metadata_requests = 0
    content_requests = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal metadata_requests, content_requests
        paper_id = request.url.path.rsplit("/", 1)[-1].split(".", 1)[0]
        if request.url.host == "api.openalex.org":
            if paper_id != "W101":
                return httpx2.Response(404)
            metadata_requests += 1
            work = _work(paper_id, grobid_xml=True)
            work["updated_date"] = f"2026-08-{13 + metadata_requests}T00:00:00Z"
            return httpx2.Response(200, json=work)
        content_requests += 1
        return httpx2.Response(
            200,
            content=b"not XML" if content_requests == 1 else TEI_CONTENT,
            headers={"Content-Type": "application/xml"},
        )

    cache_directory = tmp_path / "ingested"
    _mock_ingestion(handler, cache_directory)
    with TestClient(app) as client:
        first_response = _post_ingest(client)
        second_response = _post_ingest(client)

    assert first_response.json()["papers"][0]["status"] == "abstract_fallback"
    assert second_response.json()["papers"][0]["status"] == "ingested"
    assert second_response.json()["papers"][0]["from_cache"] is False
    assert content_requests == 2


def test_abstract_cache_can_upgrade_to_fulltext_when_key_becomes_available(
    tmp_path: Path,
) -> None:
    content_requests = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal content_requests
        paper_id = request.url.path.rsplit("/", 1)[-1].split(".", 1)[0]
        if request.url.host == "api.openalex.org":
            return httpx2.Response(
                200,
                json=_work(
                    paper_id,
                    grobid_xml=True,
                    grobid_url=canonical_content_url(paper_id, "grobid_xml"),
                ),
            )
        content_requests += 1
        return httpx2.Response(
            200,
            content=TEI_CONTENT,
            headers={"Content-Type": "application/xml"},
        )

    cache_directory = tmp_path / "ingested"
    _mock_ingestion(handler, cache_directory, api_key=None)
    with TestClient(app) as client:
        fallback_response = _post_ingest(client)

    _mock_ingestion(handler, cache_directory)
    with TestClient(app) as client:
        fulltext_response = _post_ingest(client)

    assert all(
        paper["status"] == "abstract_fallback"
        for paper in fallback_response.json()["papers"]
    )
    assert all(
        paper["status"] == "ingested"
        and paper["source_type"] == "grobid_xml"
        for paper in fulltext_response.json()["papers"]
    )
    assert content_requests == 3


def test_noncanonical_content_url_is_never_requested(tmp_path: Path) -> None:
    content_requests = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal content_requests
        if request.url.host == "content.openalex.org":
            content_requests += 1
            raise AssertionError("Noncanonical metadata URL triggered content access")
        assert request.url.host == "api.openalex.org"
        paper_id = request.url.path.rsplit("/", 1)[-1]
        return httpx2.Response(
            200,
            json=_work(
                paper_id,
                abstract=False,
                grobid_xml=True,
                grobid_url=f"https://example.org/{paper_id}.xml",
            ),
        )

    _mock_ingestion(handler, tmp_path / "ingested")
    with TestClient(app) as client:
        response = _post_ingest(client)

    assert response.status_code == 200
    assert all(
        paper["status"] == "unavailable"
        for paper in response.json()["papers"]
    )
    assert content_requests == 0


def test_ingest_reuses_selection_validation_without_network(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(f"Unexpected request to {request.url.host}")

    _mock_ingestion(handler, tmp_path / "ingested")
    with TestClient(app) as client:
        too_few = client.post(
            "/papers/ingest", json={"paper_ids": ["W101", "W102"]}
        )
        duplicate = client.post(
            "/papers/ingest",
            json={
                "paper_ids": [
                    "W101",
                    "https://openalex.org/W101",
                    "W103",
                ]
            },
        )
        arbitrary_url = client.post(
            "/papers/ingest",
            json={
                "paper_ids": [
                    "W101",
                    "https://example.org/paper.pdf",
                    "W103",
                ]
            },
        )

    assert too_few.status_code == 422
    assert duplicate.status_code == 422
    assert arbitrary_url.status_code == 422


def test_safe_tei_parser_rejects_entity_expansion() -> None:
    malicious_xml = b"""<?xml version="1.0"?>
<!DOCTYPE TEI [<!ENTITY secret SYSTEM "file:///etc/passwd">]>
<TEI><text><body><div><p>&secret;</p></div></body></text></TEI>
"""

    with pytest.raises(IngestionParseError):
        parse_tei_xml(malicious_xml)
