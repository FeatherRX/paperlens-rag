import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


OPENALEX_WORK_ID_PATTERN = re.compile(
    r"^(?:https://openalex\.org/)?(W[1-9]\d*)$"
)


class OpenAccessInfo(BaseModel):
    is_oa: bool | None = None
    status: str | None = None
    oa_url: str | None = None
    any_repository_has_fulltext: bool | None = None


class Paper(BaseModel):
    id: str | None = None
    title: str | None = None
    authors: list[str]
    institutions: list[str]
    publication_year: int | None = None
    abstract: str | None = None
    doi: str | None = None
    landing_page_url: str | None = None
    open_access: OpenAccessInfo | None = None
    cited_by_count: int | None = None


class PaperSearchResponse(BaseModel):
    query: str
    count: int
    papers: list[Paper]


SourceStatus = Literal[
    "fulltext_candidate",
    "abstract_only",
    "unavailable",
]


class OpenAlexContent(BaseModel):
    pdf_available: bool
    grobid_xml_available: bool
    content_url: str | None = None


class PreparedPaper(Paper):
    source_status: SourceStatus
    fulltext_url: str | None = None
    fulltext_license: str | None = None
    openalex_content: OpenAlexContent


class PaperPrepareRequest(BaseModel):
    paper_ids: list[str] = Field(min_length=3, max_length=5)

    @field_validator("paper_ids")
    @classmethod
    def normalize_paper_ids(cls, paper_ids: list[str]) -> list[str]:
        normalized_ids: list[str] = []
        for paper_id in paper_ids:
            match = OPENALEX_WORK_ID_PATTERN.fullmatch(paper_id.strip())
            if match is None:
                raise ValueError("paper_ids must contain valid OpenAlex work IDs")
            normalized_ids.append(match.group(1))

        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("paper_ids must not contain duplicates")

        return normalized_ids


class PaperPrepareResponse(BaseModel):
    count: int
    papers: list[PreparedPaper]


class PaperIngestRequest(PaperPrepareRequest):
    """The ingest endpoint deliberately reuses paper selection validation."""


IngestStatus = Literal[
    "ingested",
    "cached",
    "abstract_fallback",
    "license_review_required",
    "unavailable",
    "failed",
]

IngestSourceType = Literal["grobid_xml", "pdf", "abstract"]


class IngestedPaperSummary(BaseModel):
    paper_id: str
    title: str | None = None
    status: IngestStatus
    source_type: IngestSourceType | None = None
    license: str | None = None
    segment_count: int = 0
    character_count: int = 0
    from_cache: bool = False
    message: str


class PaperIngestResponse(BaseModel):
    count: int
    papers: list[IngestedPaperSummary]


class RagAnswerRequest(PaperPrepareRequest):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, query: str) -> str:
        normalized = query.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized


class RagCitation(BaseModel):
    citation_number: int = Field(ge=1)
    paper_id: str
    paper_title: str | None = None
    chunk_index: int = Field(ge=0)
    page_numbers: list[int] = Field(default_factory=list)
    section_title: str | None = None
    evidence_excerpt: str = Field(min_length=1)
    retrieval_score: float


class RagAnswerResponse(BaseModel):
    answer: str = Field(min_length=1)
    citations: list[RagCitation]
