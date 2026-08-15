from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status

from app.answer import AnswerGenerationError, AnswerService
from app.chunking import ChunkingService
from app.embedding import EmbeddingError, EmbeddingService
from app.ingestion import PaperIngestionService
from app.models import (
    PaperIngestRequest,
    PaperIngestResponse,
    PaperPrepareRequest,
    PaperPrepareResponse,
    PaperSearchResponse,
    RagAnswerRequest,
    RagAnswerResponse,
)
from app.openalex import (
    OpenAlexClient,
    OpenAlexError,
    OpenAlexNotFoundError,
    OpenAlexTimeoutError,
)
from app.openalex import get_openalex_client
from app.qwen import QwenConfigurationError, QwenLLMClient, QwenTimeoutError
from app.rag import (
    EmptyRagCorpusError,
    IngestedPapersNotFoundError,
    RagAnswerService,
)
from app.retrieval import RetrievalError, RetrievalService


app = FastAPI(title="PaperLens RAG")


def get_ingestion_service(
    openalex_client: Annotated[OpenAlexClient, Depends(get_openalex_client)],
) -> PaperIngestionService:
    return PaperIngestionService(openalex_client)


def get_qwen_client() -> Iterator[QwenLLMClient]:
    try:
        client = QwenLLMClient.from_env()
    except QwenConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "llm_configuration_error",
                "message": "DashScope configuration is missing or invalid",
            },
        ) from exc
    try:
        yield client
    finally:
        client.close()


def get_rag_answer_service(
    qwen_client: Annotated[QwenLLMClient, Depends(get_qwen_client)],
) -> RagAnswerService:
    embedding_service = EmbeddingService()
    return RagAnswerService(
        AnswerService(qwen_client),
        chunking_service=ChunkingService(),
        embedding_service=embedding_service,
        retrieval_service=RetrievalService(embedding_service),
    )


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


@app.post("/rag/answer", response_model=RagAnswerResponse)
def answer_rag(
    request: RagAnswerRequest,
    rag_service: Annotated[
        RagAnswerService, Depends(get_rag_answer_service)
    ],
) -> RagAnswerResponse:
    try:
        return rag_service.generate_answer(
            request.query,
            request.paper_ids,
            top_k=request.top_k,
        )
    except IngestedPapersNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "ingested_papers_not_found",
                "paper_ids": exc.paper_ids,
            },
        ) from exc
    except EmptyRagCorpusError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "rag_corpus_empty",
                "message": str(exc),
            },
        ) from exc
    except AnswerGenerationError as exc:
        is_timeout = isinstance(exc.__cause__, QwenTimeoutError)
        raise HTTPException(
            status_code=(
                status.HTTP_504_GATEWAY_TIMEOUT
                if is_timeout
                else status.HTTP_502_BAD_GATEWAY
            ),
            detail={
                "code": (
                    "llm_request_timeout"
                    if is_timeout
                    else "llm_request_failed"
                ),
                "message": (
                    "The answer model request timed out"
                    if is_timeout
                    else "The answer model request failed"
                ),
            },
        ) from exc
    except (EmbeddingError, RetrievalError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "rag_pipeline_failed",
                "message": "The local RAG pipeline failed",
            },
        ) from exc
