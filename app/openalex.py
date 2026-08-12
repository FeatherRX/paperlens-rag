import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
from dotenv import load_dotenv

from app.models import OpenAccessInfo, Paper


OPENALEX_BASE_URL = "https://api.openalex.org"
OPENALEX_TIMEOUT_SECONDS = 10.0
DEFAULT_DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"
OPENALEX_WORK_FIELDS = (
    "id,title,display_name,authorships,publication_year,"
    "abstract_inverted_index,doi,primary_location,best_oa_location,"
    "open_access,cited_by_count"
)


class OpenAlexError(RuntimeError):
    """Raised when OpenAlex returns an invalid or unsuccessful response."""


class OpenAlexTimeoutError(OpenAlexError):
    """Raised when OpenAlex does not respond before the configured timeout."""


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


def _paper_from_work(work: dict[str, Any]) -> Paper:
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


class OpenAlexClient:
    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        api_key: str | None = None,
    ) -> None:
        self._http_client = http_client
        self._api_key = api_key

    async def search_papers(self, query: str, limit: int) -> list[Paper]:
        params: dict[str, str | int] = {
            "search": query,
            "per_page": limit,
            "select": OPENALEX_WORK_FIELDS,
        }
        if self._api_key:
            params["api_key"] = self._api_key

        try:
            response = await self._http_client.get("/works", params=params)
            response.raise_for_status()
        except httpx2.TimeoutException as exc:
            raise OpenAlexTimeoutError("OpenAlex request timed out") from exc
        except httpx2.HTTPError as exc:
            raise OpenAlexError("OpenAlex request failed") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise OpenAlexError("OpenAlex returned invalid JSON") from exc

        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise OpenAlexError("OpenAlex response did not contain a results list")

        return [_paper_from_work(work) for work in results if isinstance(work, dict)]


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
