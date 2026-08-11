from fastapi import FastAPI


app = FastAPI(title="PaperLens RAG")


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "paperlens-rag",
    }
