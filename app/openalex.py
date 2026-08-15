import gzip
import io
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx2
from dotenv import load_dotenv

from app.models import OpenAccessInfo, OpenAlexContent, Paper, PreparedPaper


OPENALEX_BASE_URL = "https://api.openalex.org"
OPENALEX_TIMEOUT_SECONDS = 10.0
OPENALEX_MAX_CONTENT_BYTES = 25 * 1024 * 1024
DEFAULT_DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"
OPENALEX_WORK_FIELDS = (
    "id,title,display_name,authorships,publication_year,"
    "abstract_inverted_index,doi,primary_location,best_oa_location,"
    "open_access,cited_by_count"
)
OPENALEX_PREPARE_WORK_FIELDS = (
    f"{OPENALEX_WORK_FIELDS},has_content,content_urls,updated_date"
)
CONTENT_HOST = "content.openalex.org"
CONTENT_BASE_URL = f"https://{CONTENT_HOST}/works"
XML_CONTENT_TYPES = frozenset(
    {"application/xml", "text/xml", "application/tei+xml"}
)
GZIP_CONTENT_TYPES = frozenset(
    {"application/gzip", "application/x-gzip", "application/octet-stream"}
)
CONTENT_WORK_ID_PATTERN = re.compile(r"^W[1-9]\d*$")
CONTENT_PATH_PATTERN = re.compile(r"^/works/W[1-9]\d*\.(?:grobid-xml|pdf)$")


class OpenAlexError(RuntimeError):
    """Raised when OpenAlex returns an invalid or unsuccessful response."""


class OpenAlexTimeoutError(OpenAlexError):
    """Raised when OpenAlex does not respond before the configured timeout."""


class OpenAlexNotFoundError(OpenAlexError):
    """Raised when an OpenAlex work ID does not exist."""

    def __init__(self, paper_id: str) -> None:
        super().__init__("OpenAlex paper not found")
        self.paper_id = paper_id


class OpenAlexContentError(OpenAlexError):
    """Raised when controlled OpenAlex content retrieval fails."""


class OpenAlexContentKeyRequiredError(OpenAlexContentError):
    """Raised before content access when no API key is configured."""


class OpenAlexContentTooLargeError(OpenAlexContentError):
    """Raised when a content response exceeds the configured size limit."""


class OpenAlexContentValidationError(OpenAlexContentError):
    """Raised when a content URL or response does not pass validation."""


def get_openalex_api_key(dotenv_path: str | os.PathLike[str] | None = None) -> str | None:
    load_dotenv(dotenv_path=dotenv_path or DEFAULT_DOTENV_PATH)
    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    return api_key or None


def reconstruct_abstract(inverted_index: object) -> str | None:
    if not isinstance(inverted_index, dict) or not inverted_index:
        return None

    positioned_words: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int) and position >= 0:
                positioned_words.append((position, word))

    if not positioned_words:
        return None

    positioned_words.sort(key=lambda item: item[0])
    return " ".join(word for _, word in positioned_words)


def _unique_display_names(items: list[object]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("display_name")
        if isinstance(name, str) and name and name not in seen:
            names.append(name)
            seen.add(name)
    return names


def paper_from_work(work: dict[str, Any]) -> Paper:
    authorships = work.get("authorships")
    if not isinstance(authorships, list):
        authorships = []

    author_entries: list[object] = []
    institution_entries: list[object] = []
    for authorship in authorships:
        if not isinstance(authorship, dict):
            continue
        author_entries.append(authorship.get("author"))
        institutions = authorship.get("institutions")
        if isinstance(institutions, list):
            institution_entries.extend(institutions)

    primary_location = work.get("primary_location")
    best_oa_location = work.get("best_oa_location")
    landing_page_url = None
    for location in (primary_location, best_oa_location):
        if isinstance(location, dict):
            candidate = location.get("landing_page_url")
            if isinstance(candidate, str) and candidate:
                landing_page_url = candidate
                break

    raw_open_access = work.get("open_access")
    open_access = None
    if isinstance(raw_open_access, dict):
        open_access = OpenAccessInfo(
            is_oa=raw_open_access.get("is_oa"),
            status=raw_open_access.get("oa_status"),
            oa_url=raw_open_access.get("oa_url"),
            any_repository_has_fulltext=raw_open_access.get(
                "any_repository_has_fulltext"
            ),
        )

    title = work.get("title") or work.get("display_name")

    return Paper(
        id=work.get("id"),
        title=title if isinstance(title, str) else None,
        authors=_unique_display_names(author_entries),
        institutions=_unique_display_names(institution_entries),
        publication_year=work.get("publication_year"),
        abstract=reconstruct_abstract(work.get("abstract_inverted_index")),
        doi=work.get("doi"),
        landing_page_url=landing_page_url,
        open_access=open_access,
        cited_by_count=work.get("cited_by_count"),
    )


def _nonempty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def is_canonical_content_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == CONTENT_HOST
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        and CONTENT_PATH_PATTERN.fullmatch(parsed.path) is not None
        and not parsed.query
        and not parsed.fragment
    )


def canonical_content_url(
    paper_id: str,
    source_type: Literal["grobid_xml", "pdf"],
) -> str:
    if CONTENT_WORK_ID_PATTERN.fullmatch(paper_id) is None:
        raise ValueError("paper_id must be a normalized OpenAlex Work ID")
    suffix = "grobid-xml" if source_type == "grobid_xml" else "pdf"
    return f"{CONTENT_BASE_URL}/{paper_id}.{suffix}"


def prepared_paper_from_work(work: dict[str, Any]) -> PreparedPaper:
    paper = paper_from_work(work)

    best_oa_location = work.get("best_oa_location")
    if not isinstance(best_oa_location, dict):
        best_oa_location = {}

    candidate_url = _nonempty_string(best_oa_location.get("pdf_url"))
    is_fulltext_candidate = (
        best_oa_location.get("is_oa") is True and candidate_url is not None
    )

    has_content = work.get("has_content")
    if not isinstance(has_content, dict):
        has_content = {}

    content_url = _nonempty_string(work.get("content_url"))
    content_urls = work.get("content_urls")
    if content_url is None and isinstance(content_urls, dict):
        content_url = _nonempty_string(content_urls.get("grobid_xml"))
        if content_url is None:
            content_url = _nonempty_string(content_urls.get("pdf"))

    if is_fulltext_candidate:
        source_status = "fulltext_candidate"
    elif paper.abstract:
        source_status = "abstract_only"
    else:
        source_status = "unavailable"

    return PreparedPaper(
        **paper.model_dump(),
        source_status=source_status,
        fulltext_url=candidate_url if is_fulltext_candidate else None,
        fulltext_license=(
            _nonempty_string(best_oa_location.get("license"))
            if is_fulltext_candidate
            else None
        ),
        openalex_content=OpenAlexContent(
            pdf_available=has_content.get("pdf") is True,
            grobid_xml_available=has_content.get("grobid_xml") is True,
            content_url=content_url,
        ),
    )


class OpenAlexClient:
    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        api_key: str | None = None,
        max_content_bytes: int = OPENALEX_MAX_CONTENT_BYTES,
    ) -> None:
        self._http_client = http_client
        self._api_key = api_key
        self._max_content_bytes = max_content_bytes

    @property
    def can_download_content(self) -> bool:
        return self._api_key is not None

    async def _get_json(
        self,
        path: str,
        params: dict[str, str | int],
        not_found_paper_id: str | None = None,
    ) -> Any:
        request_params = dict(params)
        if self._api_key:
            request_params["api_key"] = self._api_key

        try:
            response = await self._http_client.get(path, params=request_params)
            if response.status_code == 404 and not_found_paper_id is not None:
                raise OpenAlexNotFoundError(not_found_paper_id)
            response.raise_for_status()
        except OpenAlexNotFoundError:
            raise
        except httpx2.TimeoutException as exc:
            raise OpenAlexTimeoutError("OpenAlex request timed out") from exc
        except httpx2.HTTPError as exc:
            raise OpenAlexError("OpenAlex request failed") from exc

        try:
            return response.json()
        except ValueError as exc:
            raise OpenAlexError("OpenAlex returned invalid JSON") from exc

    async def search_papers(self, query: str, limit: int) -> list[Paper]:
        params: dict[str, str | int] = {
            "search": query,
            "per_page": limit,
            "select": OPENALEX_WORK_FIELDS,
        }
        payload = await self._get_json("/works", params)

        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise OpenAlexError("OpenAlex response did not contain a results list")

        try:
            return [
                paper_from_work(work) for work in results if isinstance(work, dict)
            ]
        except (TypeError, ValueError) as exc:
            raise OpenAlexError("OpenAlex returned invalid work data") from exc

    async def get_work(self, paper_id: str) -> dict[str, Any]:
        payload = await self._get_json(
            f"/works/{paper_id}",
            {"select": OPENALEX_PREPARE_WORK_FIELDS},
            not_found_paper_id=paper_id,
        )
        if not isinstance(payload, dict):
            raise OpenAlexError("OpenAlex returned invalid work data")
        return payload

    async def prepare_papers(self, paper_ids: list[str]) -> list[PreparedPaper]:
        papers: list[PreparedPaper] = []
        for paper_id in paper_ids:
            payload = await self.get_work(paper_id)
            try:
                papers.append(prepared_paper_from_work(payload))
            except (TypeError, ValueError) as exc:
                raise OpenAlexError("OpenAlex returned invalid work data") from exc

        return papers

    async def download_content(
        self,
        url: str,
        source_type: Literal["grobid_xml", "pdf"],
    ) -> bytes:
        if not self._api_key:
            raise OpenAlexContentKeyRequiredError(
                "An OpenAlex API key is required for content download"
            )
        if not is_canonical_content_url(url):
            raise OpenAlexContentValidationError(
                "OpenAlex content URL was not canonical"
            )

        try:
            async with self._http_client.stream(
                "GET",
                url,
                params={"api_key": self._api_key},
                follow_redirects=False,
                timeout=OPENALEX_TIMEOUT_SECONDS,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type")
                content_encoding = response.headers.get("content-encoding")
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        declared_length = int(content_length)
                    except ValueError:
                        declared_length = 0
                    if declared_length > self._max_content_bytes:
                        raise OpenAlexContentTooLargeError(
                            "OpenAlex content exceeded the maximum file size"
                        )

                if response.is_stream_consumed:
                    raw_content = response.content
                    if len(raw_content) > self._max_content_bytes:
                        raise OpenAlexContentTooLargeError(
                            "OpenAlex content exceeded the maximum file size"
                        )
                else:
                    chunks: list[bytes] = []
                    received = 0
                    async for chunk in response.aiter_raw():
                        received += len(chunk)
                        if received > self._max_content_bytes:
                            raise OpenAlexContentTooLargeError(
                                "OpenAlex content exceeded the maximum file size"
                            )
                        chunks.append(chunk)
                    raw_content = b"".join(chunks)
        except OpenAlexContentError:
            raise
        except httpx2.TimeoutException:
            raise OpenAlexTimeoutError("OpenAlex content request timed out") from None
        except httpx2.HTTPError:
            raise OpenAlexContentError("OpenAlex content request failed") from None

        is_gzip = (
            "gzip" in (content_encoding or "").lower()
            or raw_content.startswith(b"\x1f\x8b")
        )
        self._validate_content_type(content_type, source_type, is_gzip)
        content = self._decompress_gzip(raw_content) if is_gzip else raw_content
        self._validate_content_signature(content, source_type)
        return content

    @staticmethod
    def _validate_content_type(
        content_type: str | None,
        source_type: Literal["grobid_xml", "pdf"],
        is_gzip: bool = False,
    ) -> None:
        media_type = (content_type or "").split(";", 1)[0].strip().lower()
        expected_type = (
            media_type in XML_CONTENT_TYPES
            if source_type == "grobid_xml"
            else media_type == "application/pdf"
        )
        valid = expected_type or (is_gzip and media_type in GZIP_CONTENT_TYPES)
        if not valid:
            raise OpenAlexContentValidationError(
                "OpenAlex content returned an unexpected Content-Type"
            )

    def _decompress_gzip(self, content: bytes) -> bytes:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(content)) as gzip_file:
                decompressed = gzip_file.read(self._max_content_bytes + 1)
        except (EOFError, OSError):
            raise OpenAlexContentValidationError(
                "OpenAlex content returned invalid gzip data"
            ) from None
        if len(decompressed) > self._max_content_bytes:
            raise OpenAlexContentTooLargeError(
                "Decompressed OpenAlex content exceeded the maximum file size"
            )
        return decompressed

    @staticmethod
    def _validate_content_signature(
        content: bytes,
        source_type: Literal["grobid_xml", "pdf"],
    ) -> None:
        if source_type == "pdf":
            valid = content.startswith(b"%PDF-")
        else:
            valid = content.lstrip(b"\xef\xbb\xbf\x00\t\r\n ").startswith(b"<")
        if not valid:
            raise OpenAlexContentValidationError(
                "OpenAlex content did not match the expected file signature"
            )


async def get_openalex_client() -> AsyncIterator[OpenAlexClient]:
    headers = {"User-Agent": "paperlens-rag/0.1"}
    timeout = httpx2.Timeout(OPENALEX_TIMEOUT_SECONDS)
    async with httpx2.AsyncClient(
        base_url=OPENALEX_BASE_URL,
        headers=headers,
        timeout=timeout,
    ) as http_client:
        yield OpenAlexClient(
            http_client=http_client,
            api_key=get_openalex_api_key(),
        )
