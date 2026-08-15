from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status

from app.ingestion import PaperIngestionService
from app.models import (
    PaperIngestRequest,
    PaperIngestResponse,
    PaperPrepareRequest,
    PaperPrepareResponse,
    PaperSearchResponse,
)
from app.openalex import (
    OpenAlexClient,
    OpenAlexError,
    OpenAlexNotFoundError,
    OpenAlexTimeoutError,
)
from app.openalex import get_openalex_client


app = FastAPI(title="PaperLens RAG")


def get_ingestion_service(
    openalex_client: Annotated[OpenAlexClient, Depends(get_openalex_client)],
) -> PaperIngestionService:
    return PaperIngestionService(openalex_client)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "paperlens-rag",
    }


@app.get("/papers/search", response_model=PaperSearchResponse)
async def search_papers(
    query: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=10)] = 10,
    openalex_client: OpenAlexClient = Depends(get_openalex_client),
) -> PaperSearchResponse:
    normalized_query = query.strip()
    if not normalized_query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="query must not be blank",
        )

    try:
        papers = await openalex_client.search_papers(normalized_query, limit)
    except OpenAlexTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="OpenAlex request timed out",
        ) from exc
    except OpenAlexError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OpenAlex request failed",
        ) from exc

    return PaperSearchResponse(
        query=normalized_query,
        count=len(papers),
        papers=papers,
    )


@app.post("/papers/prepare", response_model=PaperPrepareResponse)
async def prepare_papers(
    request: PaperPrepareRequest,
    openalex_client: OpenAlexClient = Depends(get_openalex_client),
) -> PaperPrepareResponse:
    try:
        papers = await openalex_client.prepare_papers(request.paper_ids)
    except OpenAlexNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "paper_not_found",
                "paper_id": exc.paper_id,
            },
        ) from exc
    except OpenAlexTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="OpenAlex request timed out",
        ) from exc
    except OpenAlexError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OpenAlex request failed",
        ) from exc

    return PaperPrepareResponse(
        count=len(papers),
        papers=papers,
    )


@app.post("/papers/ingest", response_model=PaperIngestResponse)
async def ingest_papers(
    request: PaperIngestRequest,
    ingestion_service: Annotated[
        PaperIngestionService, Depends(get_ingestion_service)
    ],
) -> PaperIngestResponse:
    papers = await ingestion_service.ingest_papers(request.paper_ids)
    return PaperIngestResponse(count=len(papers), papers=papers)
