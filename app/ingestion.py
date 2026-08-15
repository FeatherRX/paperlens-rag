import io
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree as SafeElementTree
from defusedxml.common import DefusedXmlException
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader

from app.models import IngestedPaperSummary
from app.openalex import (
    OpenAlexClient,
    OpenAlexContentError,
    OpenAlexContentKeyRequiredError,
    OpenAlexContentTooLargeError,
    OpenAlexContentValidationError,
    OpenAlexError,
    OpenAlexNotFoundError,
    OpenAlexTimeoutError,
    canonical_content_url,
    is_canonical_content_url,
    paper_from_work,
)


ALLOWED_FULLTEXT_LICENSES = frozenset(
    {"cc-by", "cc-by-sa", "cc0", "public-domain"}
)
DEFAULT_INGESTION_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "data" / "ingested"
)
DOCUMENT_SCHEMA_VERSION = 2
FULLTEXT_FAILURE_RETRY_COOLDOWN = timedelta(hours=6)
REFERENCE_SECTION_TYPES = frozenset(
    {"references", "bibliography", "listbibl", "references-section"}
)
REFERENCE_SECTION_HEADINGS = frozenset(
    {"references", "bibliography", "works cited", "literature cited"}
)


class TextSegment(BaseModel):
    text: str
    page_number: int | None = None
    section_title: str | None = None


class FulltextAttempt(BaseModel):
    content_requested: bool
    failure_reason: str
    attempted_at: str
    source_version: str | None = None


class NormalizedDocument(BaseModel):
    schema_version: int = DOCUMENT_SCHEMA_VERSION
    paper_id: str
    title: str | None = None
    license: str | None = None
    source_type: Literal["grobid_xml", "pdf", "abstract"]
    segments: list[TextSegment]
    character_count: int
    ingested_at: str
    source_version: str | None = None
    fulltext_attempt: FulltextAttempt | None = None


class IngestionParseError(RuntimeError):
    """Raised when validated content cannot be converted into text segments."""


class JsonDocumentStore:
    def __init__(self, root: Path = DEFAULT_INGESTION_DIRECTORY) -> None:
        self.root = root

    def load(self, paper_id: str) -> NormalizedDocument | None:
        path = self.root / f"{paper_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            document = NormalizedDocument.model_validate(payload)
        except (OSError, ValueError, ValidationError):
            return None

        if (
            document.paper_id != paper_id
            or document.schema_version != DOCUMENT_SCHEMA_VERSION
            or not document.segments
        ):
            return None
        return document

    def save(self, document: NormalizedDocument) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{document.paper_id}.json"
        temporary_path: Path | None = None
        try:
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
                    document.model_dump(mode="json"),
                    temporary_file,
                    ensure_ascii=False,
                    indent=2,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, target)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


def normalize_license(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def is_allowed_fulltext_license(value: object) -> bool:
    return normalize_license(value) in ALLOWED_FULLTEXT_LICENSES


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _is_reference_container(element: Element) -> bool:
    local_name = _local_name(element.tag)
    if local_name in {"listBibl", "biblStruct"}:
        return True

    attribute_text = " ".join(
        element.get(attribute, "")
        for attribute in ("type", "role", "class", "id")
    )
    attribute_tokens = set(
        attribute_text.casefold().replace("_", "-").replace("-", " ").split()
    )
    return bool(
        attribute_tokens
        & {"reference", "references", "bibliography", "bibliographic"}
    )


def parse_tei_xml(content: bytes) -> list[TextSegment]:
    try:
        root = SafeElementTree.fromstring(content)
    except (DefusedXmlException, ParseError, ValueError) as exc:
        raise IngestionParseError("GROBID XML could not be parsed safely") from exc

    root_local_name = _local_name(root.tag)
    if root_local_name not in {"TEI", "html"}:
        raise IngestionParseError("GROBID XML root was not TEI or html")

    body = next(
        (element for element in root.iter() if _local_name(element.tag) == "body"),
        None,
    )
    if body is None:
        raise IngestionParseError("GROBID XML did not contain a text body")

    segments: list[TextSegment] = []

    def walk(element: Element, section_title: str | None) -> None:
        local_name = _local_name(element.tag)
        if _is_reference_container(element):
            return
        current_title = section_title
        if local_name == "div":
            section_type = (element.get("type") or "").strip().lower()
            if section_type in REFERENCE_SECTION_TYPES:
                return
            heading = next(
                (
                    child
                    for child in element
                    if _local_name(child.tag) == "head"
                ),
                None,
            )
            if heading is not None:
                heading_text = _normalized_text("".join(heading.itertext()))
                if heading_text:
                    if heading_text.casefold() in REFERENCE_SECTION_HEADINGS:
                        return
                    current_title = heading_text

        if local_name == "p":
            text = _normalized_text("".join(element.itertext()))
            if text:
                segments.append(
                    TextSegment(text=text, section_title=current_title)
                )
            return

        for child in element:
            if _local_name(child.tag) != "head":
                walk(child, current_title)

    walk(body, None)
    if not segments:
        raise IngestionParseError("GROBID XML did not contain paragraph text")
    return segments


def parse_pdf(content: bytes) -> list[TextSegment]:
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        segments = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = _normalized_text(page.extract_text() or "")
            if text:
                segments.append(
                    TextSegment(text=text, page_number=page_number)
                )
    except Exception as exc:
        raise IngestionParseError("PDF text could not be extracted") from exc

    if not segments:
        raise IngestionParseError("PDF did not contain extractable text")
    return segments


def _abstract_segments(abstract: str) -> list[TextSegment]:
    text = _normalized_text(abstract)
    return [TextSegment(text=text)] if text else []


def _character_count(segments: list[TextSegment]) -> int:
    return sum(len(segment.text) for segment in segments)


def _source_version(work: dict[str, object]) -> str | None:
    value = work.get("updated_date")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _content_candidates(
    work: dict[str, object],
    paper_id: str,
) -> list[tuple[Literal["grobid_xml", "pdf"], str]]:
    has_content = work.get("has_content")
    if not isinstance(has_content, dict):
        has_content = {}
    content_urls = work.get("content_urls")
    if not isinstance(content_urls, dict):
        content_urls = {}

    grobid_url = content_urls.get("grobid_xml")
    if isinstance(grobid_url, str) and grobid_url.strip():
        grobid_available = is_canonical_content_url(grobid_url)
    else:
        grobid_available = has_content.get("grobid_xml") is True

    pdf_url = content_urls.get("pdf")
    if isinstance(pdf_url, str) and pdf_url.strip():
        pdf_available = is_canonical_content_url(pdf_url)
    else:
        pdf_available = has_content.get("pdf") is True

    legacy_url = work.get("content_url")
    if isinstance(legacy_url, str):
        legacy_is_canonical = is_canonical_content_url(legacy_url)
        if legacy_url.endswith(".grobid-xml"):
            grobid_available = grobid_available or legacy_is_canonical
        elif legacy_url.endswith(".pdf"):
            pdf_available = pdf_available or legacy_is_canonical

    candidates: list[tuple[Literal["grobid_xml", "pdf"], str]] = []
    if grobid_available:
        candidates.append(
            ("grobid_xml", canonical_content_url(paper_id, "grobid_xml"))
        )
    if pdf_available:
        candidates.append(("pdf", canonical_content_url(paper_id, "pdf")))
    return candidates


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _failure_cooldown_active(
    document: NormalizedDocument,
    source_version: str | None,
    now: datetime,
) -> bool:
    attempt = document.fulltext_attempt
    if (
        attempt is None
        or not attempt.content_requested
        or attempt.source_version != source_version
    ):
        return False
    attempted_at = _parse_timestamp(attempt.attempted_at)
    if attempted_at is None:
        return False
    elapsed = now - attempted_at
    return elapsed < FULLTEXT_FAILURE_RETRY_COOLDOWN


class PaperIngestionService:
    def __init__(
        self,
        openalex_client: OpenAlexClient,
        store: JsonDocumentStore | None = None,
    ) -> None:
        self._openalex_client = openalex_client
        self._store = store or JsonDocumentStore()

    async def ingest_papers(
        self, paper_ids: list[str]
    ) -> list[IngestedPaperSummary]:
        results: list[IngestedPaperSummary] = []
        for paper_id in paper_ids:
            results.append(await self._ingest_one(paper_id))
        return results

    async def _ingest_one(self, paper_id: str) -> IngestedPaperSummary:
        try:
            work = await self._openalex_client.get_work(paper_id)
        except OpenAlexNotFoundError:
            return self._empty_summary(
                paper_id,
                "unavailable",
                "OpenAlex work was not found.",
            )
        except OpenAlexTimeoutError:
            return self._empty_summary(
                paper_id,
                "failed",
                "OpenAlex metadata request timed out.",
            )
        except OpenAlexError:
            return self._empty_summary(
                paper_id,
                "failed",
                "OpenAlex metadata request failed.",
            )

        try:
            paper = paper_from_work(work)
        except (TypeError, ValueError):
            return self._empty_summary(
                paper_id,
                "failed",
                "OpenAlex returned invalid work metadata.",
            )
        title = paper.title
        abstract = paper.abstract
        best_oa_location = work.get("best_oa_location")
        if not isinstance(best_oa_location, dict):
            best_oa_location = {}
        license_value = normalize_license(best_oa_location.get("license"))
        license_allowed = (
            best_oa_location.get("is_oa") is True
            and is_allowed_fulltext_license(license_value)
        )

        if not license_allowed:
            if abstract:
                return self._save_abstract_fallback(
                    paper_id,
                    title,
                    license_value,
                    abstract,
                    _source_version(work),
                    "Full-text license requires review; stored the original abstract only.",
                )
            return self._empty_summary(
                paper_id,
                "license_review_required",
                "Full-text license is missing, unknown, or not allowed.",
                title=title,
                license_value=license_value,
            )

        source_version = _source_version(work)
        candidates = _content_candidates(work, paper_id)
        cached = self._store.load(paper_id)
        recent_paid_failure = cached is not None and _failure_cooldown_active(
            cached, source_version, datetime.now(UTC)
        )
        cache_can_satisfy_request = cached is not None and (
            cached.source_type in {"grobid_xml", "pdf"}
            or recent_paid_failure
            or not candidates
            or not self._openalex_client.can_download_content
        )
        if (
            cached is not None
            and cached.license == license_value
            and cache_can_satisfy_request
        ):
            return self._summary_from_document(
                cached,
                status="cached",
                title=title,
                from_cache=True,
                message=(
                    "Used the cached abstract after a recent full-text failure; no new content request was made."
                    if recent_paid_failure
                    else "Used the existing normalized document cache."
                ),
            )

        if not candidates:
            if abstract:
                return self._save_abstract_fallback(
                    paper_id,
                    title,
                    license_value,
                    abstract,
                    source_version,
                    "No canonical OpenAlex full-text content was available; stored the original abstract.",
                    failure_reason="content_unavailable",
                    content_requested=False,
                )
            return self._empty_summary(
                paper_id,
                "unavailable",
                "No canonical OpenAlex full text or abstract was available.",
                title=title,
                license_value=license_value,
            )

        if not self._openalex_client.can_download_content:
            if abstract:
                return self._save_abstract_fallback(
                    paper_id,
                    title,
                    license_value,
                    abstract,
                    source_version,
                    "Content download requires a configured OpenAlex API key; stored the original abstract.",
                    failure_reason="api_key_required",
                    content_requested=False,
                )
            return self._empty_summary(
                paper_id,
                "unavailable",
                "Content download requires a configured OpenAlex API key and no abstract was available.",
                title=title,
                license_value=license_value,
            )

        source_type, content_url = candidates[0]
        try:
            content = await self._openalex_client.download_content(
                content_url, source_type
            )
            segments = (
                parse_tei_xml(content)
                if source_type == "grobid_xml"
                else parse_pdf(content)
            )
            document = self._document(
                paper_id=paper_id,
                title=title,
                license_value=license_value,
                source_type=source_type,
                segments=segments,
                source_version=source_version,
            )
            self._store.save(document)
            return self._summary_from_document(
                document,
                status="ingested",
                title=title,
                from_cache=False,
                message="Full text was downloaded, parsed, and normalized.",
            )
        except (
            OpenAlexContentKeyRequiredError,
            OpenAlexContentTooLargeError,
            OpenAlexContentValidationError,
            OpenAlexTimeoutError,
            OpenAlexContentError,
            IngestionParseError,
            OSError,
        ) as exc:
            reason = self._safe_failure_message(exc)
            failure_reason = self._failure_reason(exc)
            if abstract:
                return self._save_abstract_fallback(
                    paper_id,
                    title,
                    license_value,
                    abstract,
                    source_version,
                    f"{reason} Stored the original abstract instead.",
                    failure_reason=failure_reason,
                    content_requested=True,
                )
            return self._empty_summary(
                paper_id,
                "failed",
                reason,
                title=title,
                license_value=license_value,
            )

    def _save_abstract_fallback(
        self,
        paper_id: str,
        title: str | None,
        license_value: str | None,
        abstract: str,
        source_version: str | None,
        message: str,
        *,
        failure_reason: str = "license_review_required",
        content_requested: bool = False,
    ) -> IngestedPaperSummary:
        segments = _abstract_segments(abstract)
        if not segments:
            return self._empty_summary(
                paper_id,
                "unavailable",
                "The original abstract did not contain usable text.",
                title=title,
                license_value=license_value,
            )
        document = self._document(
            paper_id=paper_id,
            title=title,
            license_value=license_value,
            source_type="abstract",
            segments=segments,
            source_version=source_version,
            fulltext_attempt=FulltextAttempt(
                content_requested=content_requested,
                failure_reason=failure_reason,
                attempted_at=datetime.now(UTC).isoformat(),
                source_version=source_version,
            ),
        )
        try:
            self._store.save(document)
        except OSError:
            return self._empty_summary(
                paper_id,
                "failed",
                "The normalized abstract could not be stored.",
                title=title,
                license_value=license_value,
            )
        return self._summary_from_document(
            document,
            status="abstract_fallback",
            title=title,
            from_cache=False,
            message=message,
        )

    @staticmethod
    def _document(
        *,
        paper_id: str,
        title: str | None,
        license_value: str | None,
        source_type: Literal["grobid_xml", "pdf", "abstract"],
        segments: list[TextSegment],
        source_version: str | None,
        fulltext_attempt: FulltextAttempt | None = None,
    ) -> NormalizedDocument:
        return NormalizedDocument(
            paper_id=paper_id,
            title=title,
            license=license_value,
            source_type=source_type,
            segments=segments,
            character_count=_character_count(segments),
            ingested_at=datetime.now(UTC).isoformat(),
            source_version=source_version,
            fulltext_attempt=fulltext_attempt,
        )

    @staticmethod
    def _summary_from_document(
        document: NormalizedDocument,
        *,
        status: Literal["ingested", "cached", "abstract_fallback"],
        title: str | None,
        from_cache: bool,
        message: str,
    ) -> IngestedPaperSummary:
        return IngestedPaperSummary(
            paper_id=document.paper_id,
            title=title or document.title,
            status=status,
            source_type=document.source_type,
            license=document.license,
            segment_count=len(document.segments),
            character_count=document.character_count,
            from_cache=from_cache,
            message=message,
        )

    @staticmethod
    def _empty_summary(
        paper_id: str,
        status: Literal[
            "license_review_required", "unavailable", "failed"
        ],
        message: str,
        *,
        title: str | None = None,
        license_value: str | None = None,
    ) -> IngestedPaperSummary:
        return IngestedPaperSummary(
            paper_id=paper_id,
            title=title,
            status=status,
            source_type=None,
            license=license_value,
            segment_count=0,
            character_count=0,
            from_cache=False,
            message=message,
        )

    @staticmethod
    def _safe_failure_message(exc: Exception) -> str:
        if isinstance(exc, OpenAlexContentTooLargeError):
            return "Full text exceeded the maximum allowed file size."
        if isinstance(exc, OpenAlexContentValidationError):
            return "Full-text response failed URL, type, or signature validation."
        if isinstance(exc, OpenAlexTimeoutError):
            return "OpenAlex content request timed out."
        if isinstance(exc, IngestionParseError):
            return "Full text could not be parsed into normalized text."
        if isinstance(exc, OSError):
            return "The normalized document could not be stored."
        return "OpenAlex content request failed."

    @staticmethod
    def _failure_reason(exc: Exception) -> str:
        if isinstance(exc, OpenAlexContentTooLargeError):
            return "content_too_large"
        if isinstance(exc, OpenAlexContentValidationError):
            return "content_validation_failed"
        if isinstance(exc, OpenAlexTimeoutError):
            return "content_timeout"
        if isinstance(exc, IngestionParseError):
            return "content_parse_failed"
        if isinstance(exc, OSError):
            return "cache_write_failed"
        return "content_request_failed"
