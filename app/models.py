from pydantic import BaseModel


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
